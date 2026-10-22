/*
 * User.cs — a person at a firm (e.g. Sara, an auditor at Firm A).
 *
 * Agents always act on behalf of a user, and can never do more than that user.
 * TenantId says which firm the user belongs to (a foreign key to Tenant.Id).
 *
 * EntraTenantId + EntraObjectId link the user to their Microsoft Entra ID account (the token's
 * tid + oid, step 14). Null for users who only sign in with dev tokens. Warden's own ids stay
 * the same, so everything after sign-in works identically for both.
 *
 * Active = false when the firm's identity provider says they've left (SCIM, step 13):
 * Warden then refuses everything for them, and their agents' passes are revoked.
 */
namespace Warden.Core.Domain;

public class User
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public Guid TenantId { get; set; }
    public required string Email { get; set; }
    public required string Name { get; set; }
    public bool Active { get; set; } = true;
    public Guid? EntraTenantId { get; set; }
    public Guid? EntraObjectId { get; set; }
}
