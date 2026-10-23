/*
 * ToolGate.cs — the gate itself: the ONLY code path from an agent to a tool.
 *
 * Two doors lead here, and both call Run(...), so neither can skip a step:
 *   POST /tools/{tool}/invoke      (Controllers/ToolGateController.cs, plain HTTP)
 *   /mcp/engagements/{id}          (Controllers/McpController.cs, MCP tools/call)
 *
 * In order. Nothing is forwarded unless every step says yes:
 *   1. check the pass (AgentPass.Check): Warden's signature, iss, aud = warden-tools, not
 *      expired, in the logbook, not revoked                      -> else 401 invalid_token
 *   2. decide (Decision.DecideTool): tool exists, walls, membership, role (today's data),
 *      tool policy (export: humans only), the pass carries the scope -> else 403 access_denied
 *   3. log the decision in the audit log, allowed or denied. If that write fails, the request
 *      fails with 500 and nothing is forwarded: no log, no access.
 *   4. forward with a fresh 60-second token (AgentPass.CreateForward), and return the answer as-is:
 *        a built-in tool  -> plain HTTP to Tools:BaseUrl (Tickmark, until step 18b)
 *        an upstream tool -> MCP tools/call to the firm's server that owns it (UpstreamMcp.cs, step 18a)
 *      Tool server unreachable -> 502. The agent never gets data Warden didn't approve.
 *
 * Which engagement, which user, which firm: ALWAYS from the pass, never from the body.
 * A pass Warden can't read at all (bad signature, expired...) has no trusted firm to file it
 * under, so it's written to the app log instead of the audit log.
 *
 * Run returns a Result (HTTP status + body) instead of an HTTP response, so each door can
 * turn it into its own shape: a plain HTTP reply, or an MCP tool result.
 *
 * C# notes:
 *   Registered in Program.cs with AddScoped: a fresh ToolGate per request, like the
 *     controllers, so it shares that request's database session.
 *   IHttpClientFactory — hands out HttpClients configured in Program.cs (the "tools" one).
 *   ILogger — the app log (console), like Python's logging.getLogger().
 */
using System.Net.Http.Headers;
using System.Text.Json;
using Warden.Api.Data;
using Warden.Core;
using Warden.Core.Domain;

namespace Warden.Api.Auth;

public class ToolGate(WardenDbContext db, WardenSigningKey key, IHttpClientFactory http,
    IConfiguration config, ILogger<ToolGate> log)
{
    // What the gate answered: an HTTP status and a body (JSON, or whatever the tool returned).
    // McpResult = the body is an upstream MCP server's tools/call result, to pass on as-is.
    public record Result(int Status, string Body, string ContentType = "application/json", bool McpResult = false);

    // authorization = the request's Authorization header, as sent ("Bearer <pass>").
    // engagement = the engagement the caller's address is for (MCP's /mcp/engagements/{id}), if any:
    //   it can only narrow the pass, never widen it. A mismatch is a logged "no".
    public async Task<Result> Run(string authorization, string tool, JsonElement args, Guid? engagement = null)
    {
        // 1. the pass.
        if (!authorization.StartsWith("Bearer "))
            return InvalidToken("no pass");
        var check = await AgentPass.Check(authorization["Bearer ".Length..], key, db);
        if (check.Row is null)
        {
            log.LogWarning("tool gate refused {Tool}: {Reason}", tool, check.Reason);
            return InvalidToken(check.Reason!);
        }
        var pass = check.Row;

        // 2. the decision (a revoked pass, or a pass used at another engagement's address, is already a "no").
        var verdict = !check.Ok ? new Decision.Verdict(false, check.Reason!)
            : engagement is not null && engagement != pass.EngagementId ? new Decision.Verdict(false, WrongEngagement)
            : Decision.DecideTool(db, pass, tool);

        // 3. the audit log, before anything is forwarded.
        var scope = Decision.ToolScope(db, pass.TenantId, tool);
        db.AuditLog.Add(new AuditEntry
        {
            At = DateTime.UtcNow, TenantId = pass.TenantId, UserId = pass.UserId, AgentId = pass.AgentId,
            EngagementId = pass.EngagementId, Action = tool, Scope = scope,
            Allowed = verdict.Allowed, Reason = verdict.Reason, Jti = pass.Jti,
        });
        await db.SaveChangesAsync();

        if (!check.Ok)
            return InvalidToken(verdict.Reason);
        if (!verdict.Allowed)
            return Error(403, "access_denied", verdict.Reason);

        // 4. forward. scope is set: DecideTool already refused unknown tools.
        try
        {
            // An upstream MCP server's tool: MCP tools/call there, its result passed on as-is.
            if (!Permissions.Tools.ContainsKey(tool))
            {
                var server = db.UpstreamServers.Find(db.UpstreamTools.Find(pass.TenantId, tool)!.ServerId)!;
                var result = await UpstreamMcp.Request(http.CreateClient("upstream"), server,
                    AgentPass.CreateForward(pass, tool, scope!, server.Audience, key), "tools/call", new { name = tool, arguments = args });
                return new Result(200, result.GetRawText(), McpResult: true);
            }

            // A built-in tool (Tickmark, until step 18b): plain HTTP.
            var forward = new HttpRequestMessage(HttpMethod.Post, $"tools/{tool}") { Content = JsonContent.Create(args) };
            forward.Headers.Authorization = new AuthenticationHeaderValue("Bearer",
                AgentPass.CreateForward(pass, tool, scope!, config["Tools:Audience"]!, key));
            using var res = await http.CreateClient("tools").SendAsync(forward);
            return new Result((int)res.StatusCode, await res.Content.ReadAsStringAsync(),
                res.Content.Headers.ContentType?.ToString() ?? "application/json");
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException)  // down, or timed out
        {
            log.LogError(e, "tool server unreachable for {Tool}", tool);
            return Error(502, "tool_unavailable", "the tool server did not answer");
        }
    }

    // The menu for tools/list: every tool this pass may call right now (DecideTool says yes).
    //   built-in tools, described by Permissions.Tools
    //   + each of the firm's upstream servers' tools, as the SERVER describes them (asked live),
    //     kept only if an admin assigned that tool on that server. A server that's down is skipped.
    // Listing isn't an action, so nothing is written to the audit log.
    public async Task<List<object>> List(DelegatedToken pass)
    {
        var menu = Permissions.Tools
            .Where(t => Decision.DecideTool(db, pass, t.Key).Allowed)
            .Select(t => (object)new { name = t.Key, description = t.Value.Description, inputSchema = JsonDocument.Parse(t.Value.InputSchema).RootElement })
            .ToList();

        foreach (var server in db.UpstreamServers.Where(s => s.TenantId == pass.TenantId).ToList())
        {
            try
            {
                var result = await UpstreamMcp.Request(http.CreateClient("upstream"), server,
                    AgentPass.CreateForward(pass, "tools/list", "", server.Audience, key), "tools/list", new { });
                foreach (var tool in result.GetProperty("tools").EnumerateArray())
                {
                    var name = tool.GetProperty("name").GetString()!;
                    if (!Permissions.Tools.ContainsKey(name)
                        && db.UpstreamTools.Find(pass.TenantId, name)?.ServerId == server.Id
                        && Decision.DecideTool(db, pass, name).Allowed)
                        menu.Add(tool);
                }
            }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException or KeyNotFoundException or InvalidOperationException)
            {
                log.LogWarning(e, "upstream {Server} left out of tools/list", server.Name);
            }
        }
        return menu;
    }

    public const string WrongEngagement = "pass is for a different engagement";

    static Result InvalidToken(string reason) => Error(401, "invalid_token", reason);

    static Result Error(int status, string error, string reason) =>
        new(status, JsonSerializer.Serialize(new { error, reason }));
}
