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
using System.Security.Cryptography;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Protocols;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using Microsoft.IdentityModel.Tokens;
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
    public const string ScimSecret = "firm-a-scim-secret-for-tests";
    public static readonly Guid FakeEntraTenant = Guid.Parse("e17a0000-0000-0000-0000-000000000001");
    public const string FakeEntraClient = "warden-test-api";
    public static readonly RsaSecurityKey FakeEntraKey = new(RSA.Create(2048)) { KeyId = "fake-entra" };

    // A token as Entra would issue it for Warden (tests change the parts they want wrong).
    public static string EntraToken(Guid oid, Guid? tid = null, string audience = FakeEntraClient, SecurityKey? key = null)
    {
        var t = tid ?? FakeEntraTenant;
        return new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = $"https://login.microsoftonline.com/{t}/v2.0",
            Audience = audience,
            Claims = new Dictionary<string, object> { ["oid"] = oid.ToString(), ["tid"] = t.ToString() },
            Expires = DateTime.UtcNow.AddHours(1),
            SigningCredentials = new SigningCredentials(key ?? FakeEntraKey, SecurityAlgorithms.RsaSha256),
        });
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseSetting("ConnectionStrings:Warden", TestDb);
        builder.UseSetting($"Scim:Tenants:{DemoSeed.FirmA}", ScimSecret);  // Firm A's identity provider
        // A FAKE Entra tenant: switched on like the real one, but its "public keys" are FakeEntraKey's,
        // handed over directly instead of downloaded from Microsoft.
        builder.UseSetting("Entra:TenantId", FakeEntraTenant.ToString());
        builder.UseSetting("Entra:ClientId", FakeEntraClient);
        // The "tools" HttpClient sends to FakeTools instead of the network.
        builder.ConfigureTestServices(s =>
        {
            s.AddHttpClient("tools").ConfigurePrimaryHttpMessageHandler(() => Tools);
            s.PostConfigure<JwtBearerOptions>(EntraSignIn.Scheme, o => o.ConfigurationManager =
                new StaticConfigurationManager<OpenIdConnectConfiguration>(new OpenIdConnectConfiguration { SigningKeys = { FakeEntraKey } }));
        });
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
    // The agent is Tickmark unless a test passes another one (e.g. from NewAgent).
    public async Task<string> Pass(Guid userId, Guid tenantId, Guid engagementId, string scope, Guid? agentId = null)
    {
        var res = await Exchange(userId, tenantId, agentId ?? DemoSeed.Tickmark, engagementId, scope);
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

    // A new agent registered for `allowedScopes`, made for one test. Used to check the layers
    // BEHIND the agent's ceiling, e.g. a careless registration that allows documents.export.
    public Guid NewAgent(string allowedScopes) => WithDb(db =>
    {
        var a = new Agent { Name = "Overreach", AllowedScopes = allowedScopes };
        db.Agents.Add(a);
        db.SaveChanges();
        return a.Id;
    });

    // A new active Staff member of Coke at Firm A, made for one test (so deactivating them
    // can't break other tests that use the demo people).
    public Guid NewCokeStaff() => WithDb(db =>
    {
        var u = new User { TenantId = DemoSeed.FirmA, Email = $"{Guid.NewGuid()}@firma.test", Name = "Leaver" };
        db.Users.Add(u);
        db.Members.Add(new Member { EngagementId = DemoSeed.Coke, UserId = u.Id, Role = Role.Staff });
        db.SaveChanges();
        return u.Id;
    });

    // What Entra does when someone leaves: SCIM PATCH active=false (as a string, like Entra).
    public Task<HttpResponseMessage> ScimDeactivate(Guid userId, string secret = ScimSecret)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", secret);
        return client.PatchAsJsonAsync($"/scim/v2/Users/{userId}", new
        {
            schemas = new[] { "urn:ietf:params:scim:api:messages:2.0:PatchOp" },
            Operations = new[] { new { op = "Replace", path = "active", value = "False" } },
        });
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
