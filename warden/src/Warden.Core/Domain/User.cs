/*
 * User.cs — a person at a firm (e.g. Sara, an auditor at Firm A).
 *
 * Agents always act on behalf of a user, and can never do more than that user.
 * TenantId says which firm the user belongs to (a foreign key to Tenant.Id).
 */
namespace Warden.Core.Domain;

public class User
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid TenantId { get; set; }
    public required string Email { get; set; }
    public required string Name { get; set; }
}
