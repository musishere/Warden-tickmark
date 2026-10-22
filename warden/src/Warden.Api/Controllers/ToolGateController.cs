/*
 * ToolGateController.cs — POST /tools/{tool}/invoke: the ONLY way an agent reaches a tool.
 *
 *   Authorization: Bearer <agent pass>        (from /token/exchange, NOT a sign-in token)
 *   { ...the tool's own arguments... }        (passed through untouched)
 *
 * In order. Nothing is forwarded unless every step says yes:
 *   1. check the pass (AgentPass.Check): Warden's signature, iss, aud = warden-tools, not
 *      expired, in the logbook, not revoked                      -> else 401 invalid_token
 *   2. decide (Decision.DecideTool): tool exists, walls, membership, role (today's data),
 *      tool policy (export: humans only), the pass carries the scope -> else 403 access_denied
 *   3. log the decision in the audit log, allowed or denied. If that write fails, the request
 *      fails with 500 and nothing is forwarded: no log, no access.
 *   4. forward to the tool server (Tools:BaseUrl, i.e. Tickmark) with a fresh 60-second token
 *      (AgentPass.CreateForward), and return the tool's answer as-is.
 *      Tool server unreachable -> 502. The agent never gets data Warden didn't approve.
 *
 * Which engagement, which user, which firm: ALWAYS from the pass, never from the body.
 * A pass Warden can't read at all (bad signature, expired...) has no trusted firm to file it
 * under, so it's written to the app log instead of the audit log.
 *
 * C# notes:
 *   [AllowAnonymous] — skip the sign-in token check (Program.cs's fallback policy): this
 *     endpoint takes an agent PASS, which it checks itself in step 1.
 *   async Task<IActionResult> / await — like Python's async def / await.
 *   IHttpClientFactory — hands out HttpClients configured in Program.cs (the "tools" one).
 *   ILogger — the app log (console), like Python's logging.getLogger().
 */
using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;
using Warden.Api.Data;
using Warden.Core;
using Warden.Core.Domain;

namespace Warden.Api.Controllers;

[ApiController]
[Route("tools/{tool}/invoke")]
[AllowAnonymous]
public class ToolGateController(WardenDbContext db, WardenSigningKey key, IHttpClientFactory http,
    IConfiguration config, ILogger<ToolGateController> log) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Invoke(string tool, [FromBody] JsonElement args)
    {
        // 1. the pass.
        var header = Request.Headers.Authorization.ToString();
        if (!header.StartsWith("Bearer "))
            return InvalidToken("no pass");
        var check = await AgentPass.Check(header["Bearer ".Length..], key, db);
        if (check.Row is null)
        {
            log.LogWarning("tool gate refused {Tool}: {Reason}", tool, check.Reason);
            return InvalidToken(check.Reason!);
        }
        var pass = check.Row;

        // 2. the decision (a revoked pass is already a "no").
        var verdict = check.Ok ? Decision.DecideTool(db, pass, tool) : new Decision.Verdict(false, check.Reason!);

        // 3. the audit log, before anything is forwarded.
        Permissions.Tools.TryGetValue(tool, out var rule);
        db.AuditLog.Add(new AuditEntry
        {
            At = DateTime.UtcNow, TenantId = pass.TenantId, UserId = pass.UserId, AgentId = pass.AgentId,
            EngagementId = pass.EngagementId, Action = tool, Scope = rule?.Scope,
            Allowed = verdict.Allowed, Reason = verdict.Reason, Jti = pass.Jti,
        });
        await db.SaveChangesAsync();

        if (!check.Ok)
            return InvalidToken(verdict.Reason);
        if (!verdict.Allowed)
            return StatusCode(403, new { error = "access_denied", reason = verdict.Reason });

        // 4. forward. rule is set: DecideTool already refused unknown tools.
        var forward = new HttpRequestMessage(HttpMethod.Post, $"tools/{tool}") { Content = JsonContent.Create(args) };
        forward.Headers.Authorization = new AuthenticationHeaderValue("Bearer",
            AgentPass.CreateForward(pass, tool, rule!.Scope, config["Tools:Audience"]!, key));
        try
        {
            using var res = await http.CreateClient("tools").SendAsync(forward);
            return new ContentResult
            {
                StatusCode = (int)res.StatusCode,
                Content = await res.Content.ReadAsStringAsync(),
                ContentType = res.Content.Headers.ContentType?.ToString() ?? "application/json",
            };
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException)  // down, or timed out
        {
            log.LogError(e, "tool server unreachable for {Tool}", tool);
            return StatusCode(502, new { error = "tool_unavailable", reason = "the tool server did not answer" });
        }
    }

    IActionResult InvalidToken(string reason) => Unauthorized(new { error = "invalid_token", reason });
}
