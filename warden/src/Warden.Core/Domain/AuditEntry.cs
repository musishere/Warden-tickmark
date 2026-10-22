/*
 * AuditEntry.cs — one row in the audit log: "who tried what, and what Warden said".
 *
 * Every decision is logged, allowed AND denied: a denied attempt is often the most
 * interesting row (an agent poking at Pepsi, a prompt injection asking for an export).
 *
 * Append-only: a database trigger refuses UPDATE, DELETE and TRUNCATE on audit_log,
 * so not even a bug (or an attacker with the app's DB login) can rewrite history.
 *
 * No foreign keys on purpose: a denied attempt may name an agent or engagement that
 * doesn't exist (that's WHY it was denied), and the row must still be written.
 *
 * C# notes:
 *   long Id — a 64-bit number the database counts up (1, 2, 3...), so rows have an order.
 *   Guid? / string? — may be null (e.g. Jti is only set when a pass was issued or used).
 */
namespace Warden.Core.Domain;

public class AuditEntry
{
    public long Id { get; set; }
    public DateTime At { get; set; }
    public Guid TenantId { get; set; }       // the caller's firm, from a verified token
    public Guid UserId { get; set; }         // on whose behalf
    public Guid? AgentId { get; set; }       // who acted (null = the user themselves)
    public Guid EngagementId { get; set; }
    public required string Action { get; set; }  // "token.exchange" now; tool names from step 9
    public string? Scope { get; set; }       // what was asked for
    public bool Allowed { get; set; }
    public required string Reason { get; set; }  // Decision's reason, e.g. "ethical wall: ..."
    public Guid? Jti { get; set; }           // the pass issued / used, if any
}
