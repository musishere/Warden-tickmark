/*
 * TokenExchangeController.cs — POST /token/exchange: trade a user's sign-in token
 * for a narrow, 15-minute agent pass. (Shape follows OAuth Token Exchange, RFC 8693.)
 *
 * Request (the agent calls this):
 *   Authorization: Bearer <Sara's sign-in token>        <- WHO the agent works for, and WHICH
 *                                                          agent (the token's azp, step 17a)
 *   { "resource": "<Coke>", "scope": "documents.read vouch.run" }
 *   (an "agent_id" in the body is ignored: an agent can't name itself)
 *
 * Checks, in order. The first "no" ends it with 403 and a reason:
 *   1. scope is not empty and only has known scopes   (else 400: a malformed request)
 *   2. the agent the sign-in token was issued to is registered, for these scopes
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
 * Checks 2-7 live in the shared decision function (Auth/Decision.cs), so the tool gate
 * (step 9) applies exactly the same rules. Issuing (audit + logbook) is Auth/Delegation.cs,
 * shared with MCP sign-in (step 17).
 */
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;
using Warden.Api.Data;
using Warden.Core;

namespace Warden.Api.Controllers;

// The JSON body. [JsonPropertyName] maps "resource" in JSON to Resource in C#.
public record ExchangeRequest(
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

        // 2-7 + audit + logbook (Auth/Delegation.cs).
        var (verdict, row) = Delegation.Issue(db, config, tenantId, userId, Delegation.AgentOf(db, User), req.Resource, asked);
        if (row is null)
            return StatusCode(403, new { error = "access_denied", reason = verdict.Reason });

        return Ok(new
        {
            access_token = AgentPass.Create(row, signingKey),
            issued_token_type = "urn:ietf:params:oauth:token-type:jwt",
            token_type = "Bearer",
            expires_in = (int)(row.ExpiresAt - row.IssuedAt).TotalSeconds,
            scope = row.Scope,
        });
    }
}
