/*
 * Delegation.cs — turning a user's sign-in into an agent pass. Two ways in, one set of rules:
 *
 *   POST /token/exchange   the agent asks for a pass for an engagement + scopes (step 6)
 *   MCP sign-in (step 17)  the agent just sends its sign-in token to /mcp/engagements/{id};
 *                          Warden makes the pass itself (ForSession) and the agent never sees it
 *
 * WHICH agent: the sign-in token's `azp` (the Entra app it was issued to), mapped to a row in
 * `agents` by its EntraClientId (step 17a). Never an id the agent sends. No such agent -> refused
 * ("unknown agent") until the firm's admin runs `register-agent`.
 *
 * Issue: Decision.Decide -> audit row ("token.exchange") -> logbook row, saved together (no audit
 * row, no pass). Same as the exchange always did.
 *
 * ForSession: an MCP agent sends one sign-in token on every message (for up to an hour). The first
 * message makes a pass; later ones reuse it, found by the sign-in token's id (SignInId), so one
 * session = one logbook row, not one per message. When it expires, the next message makes a new one.
 * A revoked pass stays revoked for the rest of that sign-in (the gate refuses it: "pass revoked").
 * Which scopes: everything the agent is registered for that the user's role on THIS engagement
 * allows (agent ceiling ∩ role). Nobody asks: an MCP client has no way to.
 */
using System.Security.Claims;
using Warden.Api.Data;
using Warden.Core;
using Warden.Core.Domain;

namespace Warden.Api.Auth;

public static class Delegation
{
    // The Warden agent a sign-in token was issued to, or Guid.Empty (Decide says "unknown agent").
    public static Guid AgentOf(WardenDbContext db, ClaimsPrincipal signIn) =>
        Guid.TryParse(signIn.FindFirst("azp")?.Value, out var client)
            ? db.Agents.Where(a => a.EntraClientId == client).Select(a => a.Id).SingleOrDefault()
            : Guid.Empty;

    public static (Decision.Verdict Verdict, DelegatedToken? Row) Issue(WardenDbContext db, IConfiguration config,
        Guid tenantId, Guid userId, Guid agentId, Guid engagementId, IReadOnlySet<string> scopes, string? signInId = null)
    {
        var verdict = Decision.Decide(db, tenantId, userId, agentId, engagementId, scopes);
        if (verdict.Allowed && scopes.Count == 0)
            verdict = new Decision.Verdict(false, "nothing this agent may do here");

        var now = DateTime.UtcNow;
        var scope = string.Join(' ', scopes.Order());
        var audit = new AuditEntry
        {
            At = now, TenantId = tenantId, UserId = userId, AgentId = agentId, EngagementId = engagementId,
            Action = "token.exchange", Scope = scope, Allowed = verdict.Allowed, Reason = verdict.Reason,
        };
        db.AuditLog.Add(audit);
        if (!verdict.Allowed)
        {
            db.SaveChanges();
            return (verdict, null);
        }

        var row = new DelegatedToken
        {
            TenantId = tenantId, UserId = userId, AgentId = agentId, EngagementId = engagementId, Scope = scope,
            IssuedAt = now, ExpiresAt = now.AddMinutes(config.GetValue("Token:LifetimeMinutes", 15)), SignInId = signInId,
        };
        audit.Jti = row.Jti;
        db.DelegatedTokens.Add(row);
        // One SaveChanges = one transaction: the pass and its audit row are saved together or not at all.
        db.SaveChanges();
        return (verdict, row);
    }

    // The pass for this MCP sign-in on this engagement: reused, or made now. Null + reason if refused.
    public static (string? Pass, string Reason) ForSession(WardenDbContext db, IConfiguration config, WardenSigningKey key,
        ClaimsPrincipal signIn, Guid engagementId)
    {
        var userId = Guid.Parse(signIn.FindFirst("oid")!.Value);
        var tenantId = Guid.Parse(signIn.FindFirst("tid")!.Value);
        var signInId = signIn.FindFirst("uti")?.Value ?? signIn.FindFirst("jti")?.Value;
        if (signInId is null)
            return (null, "sign-in token has no id (uti / jti)");

        var now = DateTime.UtcNow;
        var row = db.DelegatedTokens.Where(t => t.SignInId == signInId && t.EngagementId == engagementId)
            .OrderByDescending(t => t.IssuedAt).FirstOrDefault();
        if (row is not null && row.UserId == userId && (row.RevokedAt is not null || row.ExpiresAt > now))
            return (AgentPass.Create(row, key), "reused");

        var agentId = AgentOf(db, signIn);
        var scopes = Permissions.Parse(db.Agents.Find(agentId)?.AllowedScopes);
        if (db.Members.Find(engagementId, userId) is { } member)
            scopes.IntersectWith(Permissions.ScopesFor(member.Role));
        var (verdict, made) = Issue(db, config, tenantId, userId, agentId, engagementId, scopes, signInId);
        return made is null ? (null, verdict.Reason) : (AgentPass.Create(made, key), "made");
    }
}
