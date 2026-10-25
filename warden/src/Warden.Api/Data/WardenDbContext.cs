/*
 * WardenDbContext.cs — the bridge between our C# classes and Postgres.
 *
 * Like SQLAlchemy's Base + Session in one:
 *   - Each DbSet<T> below is a table. db.Users is "SELECT ... FROM users",
 *     db.Users.Add(x) + db.SaveChanges() is an INSERT.
 *   - OnModelCreating adds the rules the plain classes can't express: keys,
 *     foreign keys and unique indexes.
 *
 * `dotnet ef migrations add <Name>` reads this file and generates SQL in
 * Data/Migrations/. `dotnet ef database update` runs that SQL against Postgres.
 * In Data/Migrations/:
 *   <date>_<Name>.cs           — one per schema change, applied in date order (like Alembic revisions)
 *   <date>_<Name>.Designer.cs  — generated metadata for that migration
 *   WardenDbContextModelSnapshot.cs — EF's copy of the current schema; it diffs your
 *     classes against this to build the next migration. Rewritten every time, never edit.
 *
 * Foreign keys use Restrict, not cascade: deleting a tenant that still has users
 * fails instead of silently deleting them. An audit system must not lose rows by accident.
 *
 * The constructor's "options" carry the connection string; Program.cs supplies it.
 */
using Microsoft.EntityFrameworkCore;
using Warden.Core.Domain;

namespace Warden.Api.Data;

public class WardenDbContext(DbContextOptions<WardenDbContext> options) : DbContext(options)
{
    public DbSet<Tenant> Tenants => Set<Tenant>();
    public DbSet<User> Users => Set<User>();
    public DbSet<Agent> Agents => Set<Agent>();
    public DbSet<Engagement> Engagements => Set<Engagement>();
    public DbSet<Member> Members => Set<Member>();
    public DbSet<EthicalWall> EthicalWalls => Set<EthicalWall>();
    public DbSet<DelegatedToken> DelegatedTokens => Set<DelegatedToken>();
    public DbSet<AuditEntry> AuditLog => Set<AuditEntry>();
    public DbSet<UpstreamServer> UpstreamServers => Set<UpstreamServer>();
    public DbSet<UpstreamTool> UpstreamTools => Set<UpstreamTool>();

    protected override void OnModelCreating(ModelBuilder b)
    {
        // A user belongs to a real tenant; one email per tenant.
        b.Entity<User>().HasOne<Tenant>().WithMany().HasForeignKey(u => u.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<User>().HasIndex(u => new { u.TenantId, u.Email }).IsUnique();
        // One Warden user per Entra account (Postgres allows many rows where both are null).
        b.Entity<User>().HasIndex(u => new { u.EntraTenantId, u.EntraObjectId }).IsUnique();

        // An engagement belongs to a real tenant.
        b.Entity<Engagement>().HasOne<Tenant>().WithMany().HasForeignKey(e => e.TenantId).OnDelete(DeleteBehavior.Restrict);

        // members: one row per (engagement, user), both must exist.
        b.Entity<Member>().HasKey(m => new { m.EngagementId, m.UserId });
        b.Entity<Member>().HasOne<Engagement>().WithMany().HasForeignKey(m => m.EngagementId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<Member>().HasOne<User>().WithMany().HasForeignKey(m => m.UserId).OnDelete(DeleteBehavior.Restrict);

        // Store the role as text ("Staff") instead of a number (0), so the DB is readable.
        b.Entity<Member>().Property(m => m.Role).HasConversion<string>();

        // ethical_walls: one block per (engagement, user), both must exist.
        b.Entity<EthicalWall>().HasKey(w => new { w.EngagementId, w.UserId });
        b.Entity<EthicalWall>().HasOne<Engagement>().WithMany().HasForeignKey(w => w.EngagementId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<EthicalWall>().HasOne<User>().WithMany().HasForeignKey(w => w.UserId).OnDelete(DeleteBehavior.Restrict);

        // delegated_tokens: the pass ledger, keyed by jti. User, agent and engagement must exist.
        // The index on user_id makes "revoke all of this user's passes" (step 13) fast.
        b.Entity<DelegatedToken>().HasKey(t => t.Jti);
        b.Entity<DelegatedToken>().HasOne<Tenant>().WithMany().HasForeignKey(t => t.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<DelegatedToken>().HasOne<User>().WithMany().HasForeignKey(t => t.UserId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<DelegatedToken>().HasOne<Agent>().WithMany().HasForeignKey(t => t.AgentId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<DelegatedToken>().HasOne<Engagement>().WithMany().HasForeignKey(t => t.EngagementId).OnDelete(DeleteBehavior.Restrict);

        // audit_log: no foreign keys (see AuditEntry.cs). Indexed by (tenant, engagement)
        // because that's how GET /audit reads it. Append-only is enforced by a trigger
        // in the AuditLog migration, not here: EF can't express triggers.
        b.Entity<AuditEntry>().ToTable("audit_log");
        b.Entity<AuditEntry>().HasIndex(a => new { a.TenantId, a.EngagementId });

        // upstream_servers: a firm's own MCP servers (step 18a), one name per firm.
        // One registration per firm per Entra app (steps 17a, 22d); a sign-in session's passes, found by its token id (step 17).
        b.Entity<Agent>().HasIndex(a => new { a.TenantId, a.EntraClientId }).IsUnique();
        b.Entity<Agent>().HasOne<Tenant>().WithMany().HasForeignKey(a => a.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<DelegatedToken>().HasIndex(t => t.SignInId);

        b.Entity<UpstreamServer>().HasOne<Tenant>().WithMany().HasForeignKey(u => u.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<UpstreamServer>().HasIndex(u => new { u.TenantId, u.Name }).IsUnique();
        // One audience per server across ALL firms: a token Warden made for one firm's server is
        // never accepted by another firm's server that picked the same name.
        b.Entity<UpstreamServer>().HasIndex(u => u.Audience).IsUnique();

        // upstream_tools: one row per tool an admin allowed, keyed by (firm, tool name), so two
        // servers can't both claim the same tool name for one firm.
        b.Entity<UpstreamTool>().HasKey(t => new { t.TenantId, t.Name });
        b.Entity<UpstreamTool>().HasOne<Tenant>().WithMany().HasForeignKey(t => t.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<UpstreamTool>().HasOne<UpstreamServer>().WithMany().HasForeignKey(t => t.ServerId).OnDelete(DeleteBehavior.Restrict);
    }
}
