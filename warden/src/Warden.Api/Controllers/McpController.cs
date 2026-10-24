/*
 * McpController.cs — /mcp/engagements/{id}: the MCP door to the tool gate (step 16).
 *
 * MCP (Model Context Protocol) is how off-the-shelf agents find and call tools. Point any MCP
 * client at this address and it gets Coke's tools, through Warden, with no Warden code inside it.
 * One address per engagement, so "which client" is part of the address.
 *
 * Messages are JSON-RPC 2.0, one per POST ("Streamable HTTP", answered with plain JSON):
 *   { "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": { "name": ..., "arguments": {...} } }
 *
 *   initialize                 -> protocol version + "I offer tools"
 *   notifications/initialized  -> 202, no body (a notification has no id and gets no answer)
 *   ping                       -> {}
 *   tools/list                 -> ToolGate.List: only the tools this pass may call right now
 *                                 (built-in + the firm's upstream MCP servers' assigned tools),
 *                                 so export_documents is never listed
 *   tools/call                 -> Auth/ToolGate.cs, exactly like POST /tools/{tool}/invoke:
 *                                 pass -> decision -> audit log -> forward.
 *                                 Allowed -> the tool's answer as text. Refused -> isError: true +
 *                                 the reason, so the agent's model reads why ("ethical wall: ...").
 *   anything else              -> JSON-RPC error -32601 "method not found"
 *
 * Sign-in (step 17): every message needs "Authorization: Bearer <token>" (fail closed: even
 * initialize). The token is either
 *   - a SIGN-IN token (Entra; a dev token on a dev machine): what a stock MCP client has after
 *     the user signs in at Microsoft. Warden makes the pass itself (Auth/Delegation.cs ForSession):
 *     this user, the agent the token was issued to (azp), this engagement. Refused (wall, not a
 *     member, unknown agent...) -> 403 with the reason, and it's in the audit log.
 *   - an agent PASS from /token/exchange (agents that get their own, like tickmark's warden_client).
 * Neither -> HTTP 401 + WWW-Authenticate: Bearer resource_metadata="<where to sign in>"
 * (RFC 9728, Controllers/ProtectedResourceController.cs). That's how an MCP client knows to open
 * Microsoft's sign-in page. A pass for another engagement -> refused (403, or a logged isError for tools/call).
 *
 * C# notes:
 *   JsonElement — a read-only view of parsed JSON (like a Python dict from json.loads, but typed).
 *   TryGetProperty(name, out var x) — "if the key is there, put it in x and return true".
 *   {engagement:guid} — the route only matches if that part of the URL is a GUID.
 */
using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;
using Warden.Api.Data;

namespace Warden.Api.Controllers;

[ApiController]
[Route("mcp/engagements/{engagement:guid}")]
[AllowAnonymous]  // takes an agent PASS, checked below / by the gate, not a sign-in token
public class McpController(ToolGate gate, WardenDbContext db, WardenSigningKey key, IConfiguration config) : ControllerBase
{
    // MCP versions we answer with. A client asking for one of these gets it; otherwise our newest.
    static readonly string[] Versions = ["2025-11-25", "2025-06-18", "2025-03-26"];

    static readonly JsonElement NoArgs = JsonDocument.Parse("{}").RootElement;

    [HttpPost]
    public async Task<IActionResult> Post(Guid engagement, [FromBody] JsonElement msg)
    {
        if (msg.ValueKind != JsonValueKind.Object || !msg.TryGetProperty("method", out var m) || m.ValueKind != JsonValueKind.String)
            return RpcError(null, -32600, "invalid request");
        var method = m.GetString()!;
        var id = msg.TryGetProperty("id", out var i) ? i : (JsonElement?)null;
        var @params = msg.TryGetProperty("params", out var p) && p.ValueKind == JsonValueKind.Object ? p : NoArgs;
        var authorization = Request.Headers.Authorization.ToString();

        // A sign-in token instead of a pass: Warden makes (or reuses) the pass for this session.
        if (await SignedIn() is { } signIn)
        {
            var (pass, reason) = Delegation.ForSession(db, config, key, signIn, engagement);
            if (pass is null)
                return StatusCode(403, new { error = "access_denied", reason });
            authorization = "Bearer " + pass;
        }

        // tools/call goes straight to the gate: it checks the pass itself and logs every
        // refusal (revoked, wrong engagement...) under the right firm.
        if (method == "tools/call")
        {
            var tool = @params.TryGetProperty("name", out var n) && n.ValueKind == JsonValueKind.String ? n.GetString()! : "";
            var args = @params.TryGetProperty("arguments", out var a) && a.ValueKind == JsonValueKind.Object ? a : NoArgs;
            var result = await gate.Run(authorization, tool, args, engagement);
            if (result.Status == 401)
                return Content401(result.Body, engagement);
            if (result.McpResult)  // an upstream MCP server's own result: pass it on untouched
                return Rpc(id, JsonDocument.Parse(result.Body).RootElement);
            return Rpc(id, new
            {
                content = new[] { new { type = "text", text = result.Body } },
                isError = result.Status is < 200 or >= 300,
            });
        }

        // Everything else: a real, current pass for THIS engagement first.
        var check = authorization.StartsWith("Bearer ")
            ? await AgentPass.Check(authorization["Bearer ".Length..], key, db)
            : new AgentPass.CheckResult(false, "no pass", null);
        if (!check.Ok)
            return Content401(JsonSerializer.Serialize(new { error = "invalid_token", reason = check.Reason }), engagement);
        if (check.Row!.EngagementId != engagement)
            return StatusCode(403, new { error = "access_denied", reason = ToolGate.WrongEngagement });

        if (id is null)  // a notification (e.g. notifications/initialized): nothing to answer
            return StatusCode(202);

        return method switch
        {
            "initialize" => Rpc(id, new
            {
                protocolVersion = @params.TryGetProperty("protocolVersion", out var v) && Versions.Contains(v.GetString())
                    ? v.GetString() : Versions[0],
                capabilities = new { tools = new { } },
                serverInfo = new { name = "warden", version = "0.1" },
            }),
            "ping" => Rpc(id, new { }),
            "tools/list" => Rpc(id, new { tools = await gate.List(check.Row) }),
            _ => RpcError(id, -32601, $"method not found: {method}"),
        };
    }

    IActionResult Rpc(JsonElement? id, object result) => Ok(new { jsonrpc = "2.0", id, result });

    IActionResult RpcError(JsonElement? id, int code, string message) => Ok(new { jsonrpc = "2.0", id, error = new { code, message } });

    // The request's sign-in token, if it is one (dev issuer or Entra) that names a Warden user + firm.
    // A pass, or no token, fails both and leaves the pass path to decide.
    async Task<ClaimsPrincipal?> SignedIn()
    {
        string[] schemes = EntraSignIn.IsConfigured(config) ? [JwtBearerDefaults.AuthenticationScheme, EntraSignIn.Scheme] : [JwtBearerDefaults.AuthenticationScheme];
        foreach (var scheme in schemes)
        {
            var result = await HttpContext.AuthenticateAsync(scheme);
            if (result.Succeeded && result.Principal.HasClaim(c => c.Type == "oid") && result.Principal.HasClaim(c => c.Type == "tid"))
                return result.Principal;
        }
        return null;
    }

    // 401 with WWW-Authenticate: "you need a (valid) bearer token", and (when Entra is on) where
    // this address's sign-in is described (RFC 9728), so an MCP client can start it by itself.
    IActionResult Content401(string body, Guid engagement)
    {
        Response.Headers.WWWAuthenticate = EntraSignIn.IsConfigured(config)
            ? $"Bearer resource_metadata=\"{ProtectedResourceController.MetadataUrl(Request, $"mcp/engagements/{engagement}")}\""
            : "Bearer";
        return new ContentResult { StatusCode = 401, Content = body, ContentType = "application/json" };
    }
}
