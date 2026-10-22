/*
 * TokenExchangeController.cs — POST /token/exchange: trade a user's sign-in token
 * for a narrow, 15-minute agent pass. (Shape follows OAuth Token Exchange, RFC 8693.)
 *
 * Request (the agent calls this):
 *   Authorization: Bearer <Sara's sign-in token>        <- WHO the agent works for
 *   { "agent_id": "<Tickmark>", "resource": "<Coke>", "scope": "documents.read vouch.run" }
 *
 * Checks, in order. The first "no" ends it with 403 and a reason:
 *   1. scope is not empty and only has known scopes   (else 400: a malformed request)
 *   2. the agent is registered
 *   3. the engagement exists IN SARA'S FIRM (firm comes from her verified token, never the body)
 *   4. Sara herself is a real user of that firm
 *   5. no ethical wall between Sara and the engagement (walls beat everything)
 *   6. Sara is a member of the engagement
 *   7. every scope asked for is allowed for Sara's role -> an agent can't exceed its user
 * Every decision (allowed or denied) is written to the audit log (step 8).
 * Then: write a row in the logbook (delegated_tokens), stamp the pass, return it.
 *
 * Response (200):
 *   { "access_token": "<pass>", "issued_token_type": "urn:ietf:params:oauth:token-type:jwt",
 *     "token_type": "Bearer", "expires_in": 900, "scope": "documents.read vouch.run" }
 *
 * Known limit (MVP): the agent only SAYS who it is (agent_id); it doesn't prove it with its
 * own secret yet. The pass is still bound to Sara's verified token and Sara's rights.
 * ponytail: agent authentication (client secret/certificate) when agents run outside our control.
 *
 * Checks 2-7 live in the shared decision function (Auth/Decision.cs), so the tool gate
 * (step 9) applies exactly the same rules.
 */
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;
using Warden.Api.Data;
using Warden.Core;
using Warden.Core.Domain;

namespace Warden.Api.Controllers;

// The JSON body. [JsonPropertyName] maps "agent_id" in JSON to AgentId in C#.
public record ExchangeRequest(
    [property: JsonPropertyName("agent_id")] Guid AgentId,
    [property: JsonPropertyName("resource")] Guid Resource,
    [property: JsonPropertyName("scope")] string? Scope);

[ApiController]
[Route("token/exchange")]
public class TokenExchangeController(WardenDbContext db, WardenSigningKey signingKey, IConfiguration config) : ControllerBase
{
    [HttpPost]
    public IActionResult Exchange(ExchangeRequest req)
    {
        // Who is asking, from the VERIFIED sign-in token (step 4 already checked it).
        var userId = Guid.Parse(User.FindFirst("oid")!.Value);
        var tenantId = Guid.Parse(User.FindFirst("tid")!.Value);

        // 1. scope must be non-empty and only contain scopes Warden knows.
        var asked = Permissions.Parse(req.Scope);
        var unknown = asked.Except(Permissions.AllScopes).ToList();
        if (asked.Count == 0 || unknown.Count > 0)
            return BadRequest(new { error = "invalid_scope", reason = $"unknown or empty scope: {string.Join(' ', unknown)}" });

        // 2-7. agent, firm, user, walls, membership, role: the shared decision function.
        var verdict = Decision.Decide(db, tenantId, userId, req.AgentId, req.Resource, asked);

        // Every decision goes in the audit log, allowed or not.
        var now = DateTime.UtcNow;
        var scope = string.Join(' ', asked.Order());
        var audit = new AuditEntry
        {
            At = now, TenantId = tenantId, UserId = userId, AgentId = req.AgentId, EngagementId = req.Resource,
            Action = "token.exchange", Scope = scope, Allowed = verdict.Allowed, Reason = verdict.Reason,
        };
        db.AuditLog.Add(audit);

        if (!verdict.Allowed)
        {
            db.SaveChanges();
            return Deny(verdict.Reason);
        }

        // All checks passed: log the pass in the logbook, then stamp it.
        var row = new DelegatedToken
        {
            TenantId = tenantId, UserId = userId, AgentId = req.AgentId, EngagementId = req.Resource,
            Scope = scope,
            IssuedAt = now,
            ExpiresAt = now.AddMinutes(config.GetValue("Token:LifetimeMinutes", 15)),
        };
        audit.Jti = row.Jti;
        db.DelegatedTokens.Add(row);
        // One SaveChanges = one database transaction: the pass and its audit row are saved
        // together or not at all. If the audit write fails, no pass is issued (fail closed).
        db.SaveChanges();

        return Ok(new
        {
            access_token = AgentPass.Create(row, signingKey),
            issued_token_type = "urn:ietf:params:oauth:token-type:jwt",
            token_type = "Bearer",
            expires_in = (int)(row.ExpiresAt - row.IssuedAt).TotalSeconds,
            scope = row.Scope,
        });
    }

    IActionResult Deny(string reason) => StatusCode(403, new { error = "access_denied", reason });
}
