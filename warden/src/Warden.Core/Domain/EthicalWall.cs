/*
 * EthicalWall.cs — "this user must never touch this engagement".
 *
 * Audit firms audit competitors (e.g. Coke and Pepsi). People on the Coke team
 * must not see Pepsi's documents, so the firm puts up an ethical wall:
 * one row per (engagement, user) that is blocked.
 *
 * A wall beats everything: membership, any role, even Admin. The decision
 * function (step 7) checks walls FIRST. An agent acting for a walled user is
 * walled too, because it can never do more than its user.
 *
 * Reason is free text for the audit log, e.g. "Works on Coke 2026 (competitor)".
 */
namespace Warden.Core.Domain;

public class EthicalWall
{
    public Guid EngagementId { get; set; }
    public Guid UserId { get; set; }
    public required string Reason { get; set; }
}
