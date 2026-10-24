/*
 * DelegatedToken.cs — one row per agent pass Warden has issued (the "pass ledger").
 *
 * A pass says "agent X may act for user Y, on engagement Z, for these scopes, until T".
 * The signed pass itself goes to the agent; Warden keeps this record so it can:
 *   - revoke a pass early (step 13): set RevokedAt, and the gate refuses it from then on
 *   - revoke ALL of a user's passes when they leave the firm (SCIM, step 13)
 *   - show in the audit trail which pass every action was made with
 *
 * SignInId: when Warden made the pass itself for an MCP agent (step 17), the id of the sign-in
 * token it was made from (Entra's `uti`, a dev token's `jti`). The agent keeps sending that sign-in
 * token; Warden finds this row by it and reuses the pass. Revoked stays revoked for that sign-in.
 *
 * Jti ("JWT id") is the pass's unique id. It's written inside the signed pass too,
 * so the gate can look the pass up here on every call.
 *
 * C# notes:
 *   DateTime? — the "?" means it may be null (RevokedAt is null until revoked).
 *   Times are always UTC.
 */
namespace Warden.Core.Domain;

public class DelegatedToken
{
    public Guid Jti { get; set; } = Guid.NewGuid();
    public Guid TenantId { get; set; }
    public Guid UserId { get; set; }        // on whose behalf ("sub" in the pass)
    public Guid AgentId { get; set; }       // who acts ("act" in the pass)
    public Guid EngagementId { get; set; }  // the only engagement the pass works for
    public required string Scope { get; set; }  // space-separated, e.g. "documents.read vouch.run"
    public DateTime IssuedAt { get; set; }
    public DateTime ExpiresAt { get; set; }
    public DateTime? RevokedAt { get; set; }
    public string? SignInId { get; set; }
}
