/*
 * DatabaseTests.cs — proves the chain works end to end:
 *   C# classes -> WardenDbContext -> migration -> real Postgres tables.
 *
 * Uses a separate database, "warden_test", so your dev database ("warden") stays clean.
 * Migrate() creates warden_test if missing and applies every migration (CI starts empty).
 * Each test runs inside a transaction that is never committed, so when the test ends
 * the rows are rolled back and the database is left exactly as it was.
 *
 * Needs Postgres running: `docker compose up -d` (CI starts its own).
 */
using Microsoft.EntityFrameworkCore;
using Warden.Api.Data;
using Warden.Core.Domain;
using Xunit;

namespace Warden.Tests;

public class DatabaseTests
{
    const string TestDb = "Host=localhost;Port=5433;Database=warden_test;Username=warden;Password=warden";

    static WardenDbContext NewDb()
    {
        // Same settings as Program.cs, but pointed at the test database.
        var options = new DbContextOptionsBuilder<WardenDbContext>()
            .UseNpgsql(TestDb)
            .UseSnakeCaseNamingConvention()
            .Options;
        var db = new WardenDbContext(options);
        db.Database.Migrate();
        return db;
    }

    [Fact]
    public void member_round_trips_through_postgres()
    {
        // "using" = dispose at the end of the method (like Python's "with").
        using var db = NewDb();
        using var tx = db.Database.BeginTransaction();  // never committed -> rolled back

        var firm = new Tenant { Name = "Firm A" };
        var sara = new User { TenantId = firm.Id, Email = "sara@firma.test", Name = "Sara" };
        var coke = new Engagement { TenantId = firm.Id, Name = "Coke 2026" };
        db.AddRange(firm, sara, coke, new Member { EngagementId = coke.Id, UserId = sara.Id, Role = Role.Staff });
        db.SaveChanges();  // INSERTs (EF orders them so foreign keys are satisfied)

        // Forget what EF has in memory, so the next query really reads from Postgres.
        db.ChangeTracker.Clear();

        var member = db.Members.Single(m => m.UserId == sara.Id);  // LINQ = a query (like SQLAlchemy's filter)
        Assert.Equal(coke.Id, member.EngagementId);
        Assert.Equal(Role.Staff, member.Role);
    }

    [Fact]
    public void ethical_wall_round_trips_through_postgres()
    {
        using var db = NewDb();
        using var tx = db.Database.BeginTransaction();

        var firm = new Tenant { Name = "Firm A" };
        var sara = new User { TenantId = firm.Id, Email = "sara@firma.test", Name = "Sara" };
        var pepsi = new Engagement { TenantId = firm.Id, Name = "Pepsi 2026" };
        db.AddRange(firm, sara, pepsi, new EthicalWall { EngagementId = pepsi.Id, UserId = sara.Id, Reason = "Works on Coke 2026" });
        db.SaveChanges();
        db.ChangeTracker.Clear();

        var wall = db.EthicalWalls.Single(w => w.UserId == sara.Id);
        Assert.Equal(pepsi.Id, wall.EngagementId);
        Assert.Equal("Works on Coke 2026", wall.Reason);
    }

    [Fact]
    public void demo_seed_builds_the_demo_world_once()
    {
        using var db = NewDb();
        using var tx = db.Database.BeginTransaction();

        DemoSeed.Run(db);
        DemoSeed.Run(db);  // second run must do nothing (no duplicate-key crash)
        db.ChangeTracker.Clear();

        Assert.Equal(2, db.Tenants.Count());
        Assert.Equal(5, db.Users.Count());
        // Omar is an Admin on Pepsi AND walled from it: the case step 7 must deny.
        Assert.Equal(Role.Admin, db.Members.Single(m => m.UserId == DemoSeed.Omar && m.EngagementId == DemoSeed.Pepsi).Role);
        Assert.True(db.EthicalWalls.Any(w => w.UserId == DemoSeed.Omar && w.EngagementId == DemoSeed.Pepsi));
    }
}
