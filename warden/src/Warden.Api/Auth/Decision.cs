/*
 * Decision.cs — the ONE place Warden decides "may this agent, for this user, do this?".
 *
 * Every answer comes with a reason (it goes into the audit log in step 8).
 * The first "no" ends it. Anything unexpected is a "no" too (fail closed).
 *
 * Two ways in:
 *
 *   Decide(...)      "may Tickmark, for Sara, get these scopes on Coke?"
 *                    Used by POST /token/exchange before a pass is stamped.
 *                    Also by GET /audit with no agent (agentId null): Sara herself asking.
 *     1. the agent is registered (skipped when there is no agent), and its registration
 *        (Agent.AllowedScopes, its ceiling) covers every scope asked for
 *     2. the engagement exists IN THE USER'S FIRM (another firm's looks the same as missing)
 *     3. the user really exists in that firm, and is still active (not deactivated by SCIM)
 *     4. walls:      no ethical wall between user and engagement (beats every role, even Admin)
 *     5. membership: the user is on the engagement
 *     6. role:       the user's role allows every scope asked for (agent can't exceed its user)
 *
 *   DecideTool(...)  "may the holder of this pass call this tool?"
 *                    Used by the tool gate (step 9), after AgentPass.Check says the pass is real.
 *     0. the tool exists: an admin assigned it a scope on one of the firm's MCP servers. A tool
 *        agents must never call (e.g. an export) is simply never assigned: unknown tool
 *     1-6. Decide(...) again, with TODAY's data: a wall, role or agent-registration change made after the pass
 *          was stamped still applies, the pass doesn't freeze old rights
 *     7. scope:       the pass itself carries the scope this tool needs
 *
 * C# notes:
 *   record — a small read-only data class (like a Python @dataclass(frozen=True)).
 *   IReadOnlySet<string> — a set you can read but not change.
 */
using Warden.Api.Data;
using Warden.Core;
using Warden.Core.Domain;

namespace Warden.Api.Auth;

public static class Decision
{
    public record Verdict(bool Allowed, string Reason);

    public static Verdict Decide(WardenDbContext db, Guid tenantId, Guid userId, Guid? agentId,
        Guid engagementId, IReadOnlySet<string> scopes)
    {
        // 1. the agent, if any, is registered, and was registered for every scope asked for
        //    (its ceiling: the same for every user it works for).
        if (agentId is not null)
        {
            var agent = db.Agents.Find(agentId.Value);
            if (agent is null || agent.TenantId != tenantId)  // another firm's registration counts for nothing here
                return Deny("unknown agent");
            var beyond = scopes.Except(Permissions.Parse(agent.AllowedScopes)).ToList();
            if (beyond.Count > 0)
                return Deny($"{agent.Name} is not registered for: {string.Join(' ', beyond)}");
        }

        // 2. the engagement is in the user's own firm. tenantId always comes from a verified
        //    token (sign-in or pass), never from a request body.
        var engagement = db.Engagements.Find(engagementId);
        if (engagement is null || engagement.TenantId != tenantId)
            return Deny("engagement not found in your firm");

        // 3. the user really exists in that firm.
        var user = db.Users.Find(userId);
        if (user is null || user.TenantId != tenantId)
            return Deny("unknown user");
        if (!user.Active)
            return Deny("user deactivated");

        // 4. walls first: they beat membership and every role.
        var wall = db.EthicalWalls.Find(engagementId, userId);
        if (wall is not null)
            return Deny($"ethical wall: {wall.Reason}");

        // 5. the user must be on the engagement.
        var member = db.Members.Find(engagementId, userId);
        if (member is null)
            return Deny("not a member of this engagement");

        // 6. every scope must be allowed for the user's role.
        var notAllowed = scopes.Except(Permissions.ScopesFor(member.Role)).ToList();
        if (notAllowed.Count > 0)
            return Deny($"{member.Role} may not grant: {string.Join(' ', notAllowed)}");

        return new Verdict(true, "allowed");
    }

    // The scope a tool needs, or null if the firm has no such tool: one an admin assigned on one of
    // the firm's MCP servers (UpstreamTool, `assign-tool`).
    public static string? ToolScope(WardenDbContext db, Guid tenantId, string tool) =>
        db.UpstreamTools.Find(tenantId, tool)?.Scope;

    // `pass` is the logbook row AgentPass.Check returned: who, which agent, which engagement, which scopes.
    public static Verdict DecideTool(WardenDbContext db, DelegatedToken pass, string tool)
    {
        // 0. unknown tool (including a server's tool no admin has assigned): nothing to allow.
        if (ToolScope(db, pass.TenantId, tool) is not { } scope)
            return Deny($"unknown tool: {tool}");

        // 1-6. agent ceiling -> walls -> membership -> role, against the data as it is right now.
        var verdict = Decide(db, pass.TenantId, pass.UserId, pass.AgentId, pass.EngagementId,
            new HashSet<string> { scope });
        if (!verdict.Allowed)
            return verdict;

        // 7. the pass must carry the scope (the user may have more rights than they gave the agent).
        if (!Permissions.Parse(pass.Scope).Contains(scope))
            return Deny($"pass does not include {scope}");

        return new Verdict(true, "allowed");
    }

    static Verdict Deny(string reason) => new(false, reason);
}
