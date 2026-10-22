/*
 * DemoSeed.cs — puts the demo world into the database.
 *
 * Run it:  dotnet run --project src/Warden.Api -- seed
 *
 *   Firm A (audits two competitors)
 *     Coke 2026:  Sara (Staff), Ali (Manager), Omar (Admin)
 *     Pepsi 2026: Bilal (Staff), Omar (Admin)
 *     Walls:      Sara, Ali, Omar  ✗ Pepsi   (they work on Coke)
 *                 Bilal            ✗ Coke    (he works on Pepsi)
 *     Omar is an Admin member of Pepsi AND walled from it: the wall must win.
 *   Firm B
 *     Acme 2026:  Dana (Staff)    — must never see anything of Firm A
 *   Agent: Tickmark
 *
 * IDs are fixed (not random) so tests and Tickmark can refer to DemoSeed.Sara etc.
 * Safe to run twice: if Firm A already exists, it does nothing.
 * Deliberately NOT a migration: demo people must never reach a production database.
 */
using Warden.Core.Domain;

namespace Warden.Api.Data;

public static class DemoSeed
{
    // "static readonly" = a constant computed once (like a module-level constant in Python).
    public static readonly Guid FirmA = Guid.Parse("a0000000-0000-0000-0000-00000000000a");
    public static readonly Guid FirmB = Guid.Parse("a0000000-0000-0000-0000-00000000000b");

    public static readonly Guid Coke = Guid.Parse("e0000000-0000-0000-0000-000000000001");
    public static readonly Guid Pepsi = Guid.Parse("e0000000-0000-0000-0000-000000000002");
    public static readonly Guid Acme = Guid.Parse("e0000000-0000-0000-0000-000000000003");

    public static readonly Guid Sara = Guid.Parse("00000000-0000-0000-0000-000000000001");
    public static readonly Guid Ali = Guid.Parse("00000000-0000-0000-0000-000000000002");
    public static readonly Guid Omar = Guid.Parse("00000000-0000-0000-0000-000000000003");
    public static readonly Guid Bilal = Guid.Parse("00000000-0000-0000-0000-000000000004");
    public static readonly Guid Dana = Guid.Parse("00000000-0000-0000-0000-000000000005");

    public static readonly Guid Tickmark = Guid.Parse("a9e00000-0000-0000-0000-000000000001");

    public static void Run(WardenDbContext db)
    {
        if (db.Tenants.Any(t => t.Id == FirmA)) return;  // already seeded

        db.AddRange(
            new Tenant { Id = FirmA, Name = "Firm A" },
            new Tenant { Id = FirmB, Name = "Firm B" },

            new Engagement { Id = Coke, TenantId = FirmA, Name = "Coke 2026" },
            new Engagement { Id = Pepsi, TenantId = FirmA, Name = "Pepsi 2026" },
            new Engagement { Id = Acme, TenantId = FirmB, Name = "Acme 2026" },

            new User { Id = Sara, TenantId = FirmA, Email = "sara@firma.test", Name = "Sara" },
            new User { Id = Ali, TenantId = FirmA, Email = "ali@firma.test", Name = "Ali" },
            new User { Id = Omar, TenantId = FirmA, Email = "omar@firma.test", Name = "Omar" },
            new User { Id = Bilal, TenantId = FirmA, Email = "bilal@firma.test", Name = "Bilal" },
            new User { Id = Dana, TenantId = FirmB, Email = "dana@firmb.test", Name = "Dana" },

            new Member { EngagementId = Coke, UserId = Sara, Role = Role.Staff },
            new Member { EngagementId = Coke, UserId = Ali, Role = Role.Manager },
            new Member { EngagementId = Coke, UserId = Omar, Role = Role.Admin },
            new Member { EngagementId = Pepsi, UserId = Omar, Role = Role.Admin },
            new Member { EngagementId = Pepsi, UserId = Bilal, Role = Role.Staff },
            new Member { EngagementId = Acme, UserId = Dana, Role = Role.Staff },

            new EthicalWall { EngagementId = Pepsi, UserId = Sara, Reason = "Works on Coke 2026" },
            new EthicalWall { EngagementId = Pepsi, UserId = Ali, Reason = "Works on Coke 2026" },
            new EthicalWall { EngagementId = Pepsi, UserId = Omar, Reason = "Works on Coke 2026" },
            new EthicalWall { EngagementId = Coke, UserId = Bilal, Reason = "Works on Pepsi 2026" },

            new Agent { Id = Tickmark, Name = "Tickmark" });
        db.SaveChanges();
    }
}
