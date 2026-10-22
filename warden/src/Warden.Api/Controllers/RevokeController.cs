/*
 * RevokeController.cs — POST /token/revoke: cancel one agent pass before it expires.
 *
 *   Authorization: Bearer <Sara's sign-in token>
 *   { "jti": "<the pass id>" }
 *
 * Only the user a pass works for can revoke it: another user's pass, another firm's pass or a
 * made-up jti all get the same 404, so nobody learns which passes exist.
 * From then on the gate refuses the pass ("pass revoked"): the agent's next tool call fails, so
 * work in progress stops at that call. Revoking twice is fine (the first time stands).
 * Every revoke is in the audit log.
 * (Revoking ALL of a user's passes when they leave: ScimController.cs.)
 */
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Data;
using Warden.Core.Domain;

namespace Warden.Api.Controllers;

public record RevokeRequest([property: JsonPropertyName("jti")] Guid Jti);

[ApiController]
[Route("token/revoke")]
public class RevokeController(WardenDbContext db) : ControllerBase
{
    [HttpPost]
    public IActionResult Revoke(RevokeRequest req)
    {
        var userId = Guid.Parse(User.FindFirst("oid")!.Value);
        var tenantId = Guid.Parse(User.FindFirst("tid")!.Value);

        var pass = db.DelegatedTokens.Find(req.Jti);
        if (pass is null || pass.UserId != userId || pass.TenantId != tenantId)
            return NotFound(new { error = "not_found", reason = "no such pass of yours" });

        var now = DateTime.UtcNow;
        pass.RevokedAt ??= now;  // "??=" sets it only if it's still null
        db.AuditLog.Add(new AuditEntry
        {
            At = now, TenantId = tenantId, UserId = userId, AgentId = pass.AgentId, EngagementId = pass.EngagementId,
            Action = "token.revoke", Scope = pass.Scope, Allowed = true, Reason = "revoked by the user", Jti = pass.Jti,
        });
        db.SaveChanges();
        return Ok(new { revoked = pass.Jti, revoked_at = pass.RevokedAt });
    }
}
