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

    protected override void OnModelCreating(ModelBuilder b)
    {
        // A user belongs to a real tenant; one email per tenant.
        b.Entity<User>().HasOne<Tenant>().WithMany().HasForeignKey(u => u.TenantId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<User>().HasIndex(u => new { u.TenantId, u.Email }).IsUnique();

        // An engagement belongs to a real tenant.
        b.Entity<Engagement>().HasOne<Tenant>().WithMany().HasForeignKey(e => e.TenantId).OnDelete(DeleteBehavior.Restrict);

        // members: one row per (engagement, user), both must exist.
        b.Entity<Member>().HasKey(m => new { m.EngagementId, m.UserId });
        b.Entity<Member>().HasOne<Engagement>().WithMany().HasForeignKey(m => m.EngagementId).OnDelete(DeleteBehavior.Restrict);
        b.Entity<Member>().HasOne<User>().WithMany().HasForeignKey(m => m.UserId).OnDelete(DeleteBehavior.Restrict);

        // Store the role as text ("Staff") instead of a number (0), so the DB is readable.
        b.Entity<Member>().Property(m => m.Role).HasConversion<string>();
    }
}
