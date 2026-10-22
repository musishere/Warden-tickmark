/*
 * WardenFactory.cs — starts the API in memory, pointed at the TEST database.
 *
 * Plain WebApplicationFactory<Program> uses appsettings.json, i.e. your dev database
 * "warden". Tests that call endpoints which read/write the database use this instead:
 *   - connection string swapped to "warden_test"
 *   - migrations applied and demo data seeded once, at startup
 * Endpoint tests can't roll back like DatabaseTests do (the request runs inside the app),
 * so rows they create (e.g. logbook entries) stay in warden_test. That's fine: it's a test DB.
 *
 * Also gives tests a quick way to make a demo user's sign-in token.
 *
 * The tool server behind the gate is FAKE here (FakeTools below): tests see exactly what the
 * gate forwarded (or that it forwarded nothing), and no real Tickmark is needed.
 *
 * xUnit runs test classes in PARALLEL by default. Every class that touches the database
 * joins the "database" collection ([Collection(WardenFactory.Db)]): they share ONE factory
 * (seeded once) and run one after another, so they can't trip over each other.
 */
using System.Net.Http.Headers;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Warden.Api.Auth;
using Warden.Api.Data;
using Warden.Core.Domain;
using Xunit;

namespace Warden.Tests;

[CollectionDefinition(WardenFactory.Db)]
public class DatabaseCollection : ICollectionFixture<WardenFactory>;

public class WardenFactory : WebApplicationFactory<Program>
{
    public const string Db = "database";
    public const string TestDb = "Host=localhost;Port=5433;Database=warden_test;Username=warden;Password=warden";

    public FakeTools Tools { get; } = new();

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Warden", TestDb);
        // The "tools" HttpClient sends to FakeTools instead of the network.
        builder.ConfigureTestServices(s => s.AddHttpClient("tools").ConfigurePrimaryHttpMessageHandler(() => Tools));
    }

    public WardenFactory()
    {
        using var scope = Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
        db.Database.Migrate();
        DemoSeed.Run(db);
    }

    // A 1-hour sign-in token for a demo user, e.g. SignIn(DemoSeed.Sara, DemoSeed.FirmA).
    public string SignIn(Guid userId, Guid tenantId) =>
        DevIssuer.CreateToken(DevIssuer.SigningKey(Services.GetRequiredService<IConfiguration>()),
            userId, tenantId, "demo", DateTime.UtcNow.AddHours(1));

    // A logbook row for Sara + Tickmark on Coke, expiring at `expires` (tests tweak it).
    public static DelegatedToken SaraOnCoke(DateTime expires) => new()
    {
        TenantId = DemoSeed.FirmA, UserId = DemoSeed.Sara, AgentId = DemoSeed.Tickmark,
        EngagementId = DemoSeed.Coke, Scope = "documents.read", IssuedAt = expires.AddMinutes(-15), ExpiresAt = expires,
    };

    // Stamp a pass for a row with Warden's real key; save the row to the logbook unless told not to.
    public string Stamp(DelegatedToken row, bool saveToLogbook = true)
    {
        using var scope = Services.CreateScope();
        if (saveToLogbook)
        {
            var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
            db.DelegatedTokens.Add(row);
            db.SaveChanges();
        }
        return AgentPass.Create(row, Services.GetRequiredService<WardenSigningKey>());
    }

    // What the gate will do (step 9): check a pass.
    public async Task<AgentPass.CheckResult> Check(string pass)
    {
        using var scope = Services.CreateScope();
        return await AgentPass.Check(pass, Services.GetRequiredService<WardenSigningKey>(),
            scope.ServiceProvider.GetRequiredService<WardenDbContext>());
    }

    // Run something against the test database, e.g. warden.WithDb(db => Decision.Decide(db, ...)).
    // Func<A, B> is C#'s type for "a function from A to B" (like Callable[[A], B] in Python).
    public T WithDb<T>(Func<WardenDbContext, T> work)
    {
        using var scope = Services.CreateScope();
        return work(scope.ServiceProvider.GetRequiredService<WardenDbContext>());
    }

    // An HTTP client to the in-memory API, signed in as a demo user.
    public HttpClient ClientFor(Guid userId, Guid tenantId)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", SignIn(userId, tenantId));
        return client;
    }

    // A real pass from /token/exchange (fails the test if refused).
    public async Task<string> Pass(Guid userId, Guid tenantId, Guid engagementId, string scope)
    {
        var res = await Exchange(userId, tenantId, DemoSeed.Tickmark, engagementId, scope);
        res.EnsureSuccessStatusCode();
        return (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("access_token").GetString()!;
    }

    // What an agent does with a pass: POST /tools/{tool}/invoke.
    public Task<HttpResponseMessage> Invoke(string? pass, string tool, object? args = null)
    {
        var client = CreateClient();
        if (pass is not null)
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", pass);
        return client.PostAsJsonAsync($"/tools/{tool}/invoke", args ?? new { });
    }

    // What an agent does: POST /token/exchange with the user's sign-in token.
    public Task<HttpResponseMessage> Exchange(Guid userId, Guid tenantId, Guid agentId, Guid engagementId, string scope) =>
        ClientFor(userId, tenantId).PostAsJsonAsync("/token/exchange", new { agent_id = agentId, resource = engagementId, scope });
}

// Stands in for Tickmark's tool server: remembers the last request the gate forwarded and
// answers {"ok": true}. Set Down = true to act like a server that's not running.
public class FakeTools : HttpMessageHandler
{
    public HttpRequestMessage? Last { get; private set; }
    public string? LastBody { get; private set; }
    public bool Down { get; set; }

    public void Reset() => (Last, LastBody, Down) = (null, null, false);

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        if (Down) throw new HttpRequestException("connection refused");
        Last = request;
        LastBody = request.Content is null ? null : await request.Content.ReadAsStringAsync(ct);
        return new HttpResponseMessage(HttpStatusCode.OK) { Content = JsonContent.Create(new { ok = true }) };
    }
}
