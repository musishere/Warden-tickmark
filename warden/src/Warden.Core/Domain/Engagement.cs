/*
 * Engagement.cs — one audit job for one client (e.g. "Coke 2026" at Firm A).
 *
 * Engagements are the unit of access: a user (and their agent) can only touch
 * documents of engagements they are a Member of. Belongs to one tenant.
 */
namespace Warden.Core.Domain;

public class Engagement
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid TenantId { get; set; }
    public required string Name { get; set; }
}
