/*
 * AuditController.cs — GET /audit?engagement=<id>: the audit trail of one engagement.
 *
 * Who may read it: the same rules as everything else (Decision.Decide, with no agent):
 *   - the engagement is in YOUR firm (another firm's -> 403 "not found", nothing leaks)
 *   - no ethical wall: Sara can't read Pepsi's trail (it would show who works on Pepsi)
 *   - you are a member of the engagement (any role)
 * And you only ever see rows from your own firm: if Dana (Firm B) tried to get a pass for
 * Coke, that row belongs to Firm B's trail, not Firm A's.
 *
 * Oldest first, the order things happened.
 * ponytail: returns every row; add paging (?after=<id>&limit=) when trails get long.
 *
 * C# notes:
 *   [FromQuery] — read the value from the URL's ?engagement=..., like FastAPI's Query().
 *   .Where/.OrderBy/.Select — LINQ: EF turns it into one SQL SELECT ... WHERE ... ORDER BY.
 */
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;
using Warden.Api.Data;

namespace Warden.Api.Controllers;

[ApiController]
[Route("audit")]
public class AuditController(WardenDbContext db) : ControllerBase
{
    [HttpGet]
    public IActionResult Get([FromQuery] Guid engagement)
    {
        var userId = Guid.Parse(User.FindFirst("oid")!.Value);
        var tenantId = Guid.Parse(User.FindFirst("tid")!.Value);

        var verdict = Decision.Decide(db, tenantId, userId, agentId: null, engagement, new HashSet<string>());
        if (!verdict.Allowed)
            return StatusCode(403, new { error = "access_denied", reason = verdict.Reason });

        return Ok(db.AuditLog
            .Where(a => a.TenantId == tenantId && a.EngagementId == engagement)
            .OrderBy(a => a.Id)
            .Select(a => new
            {
                a.Id, a.At, a.UserId, a.AgentId, a.Action, a.Scope, a.Allowed, a.Reason, a.Jti,
            })
            .ToList());
    }
}
