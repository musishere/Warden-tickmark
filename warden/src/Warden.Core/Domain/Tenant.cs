/*
 * Tenant.cs — an audit firm using Warden (e.g. "Firm A").
 *
 * Everything else (users, engagements) belongs to exactly one tenant, and a
 * tenant must never see another tenant's data. Later the database table
 * "tenants" is created from this class.
 *
 * C# notes:
 *   Guid      — a random unique id (like Python's uuid.uuid4()).
 *   { get; set; } — a "property": a field you can read and write.
 *   required  — whoever creates a Tenant must set Name, or it won't compile.
 */
namespace Warden.Core.Domain;

public class Tenant
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public required string Name { get; set; }
}
