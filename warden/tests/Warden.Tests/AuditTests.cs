/*
 * AuditTests.cs — step 8: the append-only audit log and GET /audit.
 *
 *   UPDATE / DELETE / TRUNCATE on audit_log       -> Postgres refuses ("append-only")
 *   Sara gets a pass for Coke                     -> logged as allowed, with the pass's jti
 *   Sara asks for Pepsi                           -> logged as denied, with the wall reason
 *   Ali (Manager, Coke) reads Coke's trail        -> sees Sara's rows, NOT Dana's (Firm B) attempt
 *   Sara reads Pepsi's trail / Dana reads Coke's  -> 403
 *
 * warden_test keeps rows between runs (and now can't delete them!), so tests look for
 * THEIR rows (by jti, or ids written during the test) instead of counting the whole table.
 */
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Warden.Core.Domain;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class AuditTests(WardenFactory warden)
{
    [Fact]
    public void audit_log_is_append_only() => warden.WithDb(db =>
    {
        using var tx = db.Database.BeginTransaction();  // rolled back at the end: the test row disappears
        db.AuditLog.Add(new AuditEntry
        {
            At = DateTime.UtcNow, TenantId = DemoSeed.FirmA, UserId = DemoSeed.Sara, EngagementId = DemoSeed.Coke,
            Action = "test", Allowed = false, Reason = "test row",
        });
        db.SaveChanges();

        // After an error Postgres refuses everything else in the transaction, so each
        // attempt runs inside a savepoint we roll back to (like a mini-transaction).
        foreach (var sql in new[] { "UPDATE audit_log SET allowed = true", "DELETE FROM audit_log", "TRUNCATE audit_log" })
        {
            tx.CreateSavepoint("attempt");
            var error = Assert.ThrowsAny<Exception>(() => db.Database.ExecuteSqlRaw(sql));
            Assert.Contains("audit_log is append-only", error.Message);
            tx.RollbackToSavepoint("attempt");
        }
        return 0;  // WithDb wants a value back
    });

    [Fact]
    public async Task exchange_logs_allowed_and_denied_decisions()
    {
        // Only rows written after this point count (older runs left Sara/Pepsi rows too).
        var before = warden.WithDb(db => db.AuditLog.Max(a => (long?)a.Id) ?? 0);

        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");
        var body = await res.Content.ReadFromJsonAsync<JsonElement>();
        var jti = Guid.Parse(new JsonWebToken(body.GetProperty("access_token").GetString()).Id);

        await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Pepsi, "documents.read");

        var (allowed, denied) = warden.WithDb(db => (
            db.AuditLog.Single(a => a.Jti == jti),
            db.AuditLog.Single(a => a.Id > before && a.UserId == DemoSeed.Sara && a.EngagementId == DemoSeed.Pepsi)));

        Assert.True(allowed.Allowed);
        Assert.Equal("token.exchange", allowed.Action);
        Assert.Equal(DemoSeed.Tickmark, allowed.AgentId);
        Assert.Equal("documents.read", allowed.Scope);

        Assert.False(denied.Allowed);
        Assert.Null(denied.Jti);  // no pass was issued
        Assert.Equal("ethical wall: Works on Coke 2026", denied.Reason);
    }

    [Fact]
    public async Task member_sees_their_engagements_trail_but_not_other_firms_attempts()
    {
        await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");
        await warden.Exchange(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");  // denied, Firm B's row

        var res = await warden.ClientFor(DemoSeed.Ali, DemoSeed.FirmA).GetAsync($"/audit?engagement={DemoSeed.Coke}");
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        var rows = await res.Content.ReadFromJsonAsync<JsonElement[]>();

        Assert.Contains(rows!, r => r.GetProperty("userId").GetGuid() == DemoSeed.Sara && r.GetProperty("allowed").GetBoolean());
        Assert.DoesNotContain(rows!, r => r.GetProperty("userId").GetGuid() == DemoSeed.Dana);
    }

    [Fact]
    public async Task walled_or_other_firm_users_cannot_read_the_trail()
    {
        var saraOnPepsi = await warden.ClientFor(DemoSeed.Sara, DemoSeed.FirmA).GetAsync($"/audit?engagement={DemoSeed.Pepsi}");
        var danaOnCoke = await warden.ClientFor(DemoSeed.Dana, DemoSeed.FirmB).GetAsync($"/audit?engagement={DemoSeed.Coke}");

        Assert.Equal(HttpStatusCode.Forbidden, saraOnPepsi.StatusCode);
        Assert.Contains("ethical wall", await saraOnPepsi.Content.ReadAsStringAsync());
        Assert.Equal(HttpStatusCode.Forbidden, danaOnCoke.StatusCode);
        Assert.Contains("not found in your firm", await danaOnCoke.Content.ReadAsStringAsync());
    }
}
