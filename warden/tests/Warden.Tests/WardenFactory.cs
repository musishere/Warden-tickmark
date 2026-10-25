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
 * The firm's MCP servers behind the gate are FAKE here (FakeUpstream below): tests see exactly
 * what the gate forwarded (or that it forwarded nothing), and no real Tickmark is needed.
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

    public FakeUpstream Upstream { get; } = new();
    public FakeEntraTokens EntraTokens { get; } = new();
    public const string ScimSecret = "firm-a-scim-secret-for-tests";
    public static readonly Guid FakeEntraTenant = Guid.Parse("e17a0000-0000-0000-0000-000000000001");
    public const string FakeEntraClient = "warden-test-api";
    public static readonly RsaSecurityKey FakeEntraKey = new(RSA.Create(2048)) { KeyId = "fake-entra" };

    // A token as Entra would issue it for Warden, to an agent's app (azp: Tickmark's unless a test
    // says otherwise; FakeEntraClient = Warden's own admin page), with the app roles Entra assigned.
    // Tests change the parts they want wrong.
    public static string EntraToken(Guid oid, Guid? tid = null, string audience = FakeEntraClient, SecurityKey? key = null,
        string? azp = null, string[]? roles = null)
    {
        var t = tid ?? FakeEntraTenant;
        var claims = new Dictionary<string, object> { ["oid"] = oid.ToString(), ["tid"] = t.ToString(),
            ["azp"] = azp ?? DemoSeed.TickmarkClientId.ToString(), ["uti"] = Guid.NewGuid().ToString() };
        if (roles is not null)
            claims["roles"] = roles;
        return new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = $"https://login.microsoftonline.com/{t}/v2.0",
            Audience = audience,
            Claims = claims,
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
        // The "upstream" HttpClient sends to FakeUpstream instead of the network.
        builder.ConfigureTestServices(s =>
        {
            s.AddHttpClient("upstream").ConfigurePrimaryHttpMessageHandler(() => Upstream);
            s.AddHttpClient("entra").ConfigurePrimaryHttpMessageHandler(() => EntraTokens);
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

    // A 1-hour sign-in token for a demo user, e.g. SignIn(DemoSeed.Sara, DemoSeed.FirmA), issued to
    // the agent with that Entra client id (none: a person signing in themselves).
    public string SignIn(Guid userId, Guid tenantId, Guid? agentClientId = null) =>
        DevIssuer.CreateToken(DevIssuer.SigningKey(Services.GetRequiredService<IConfiguration>()),
            userId, tenantId, "demo", DateTime.UtcNow.AddHours(1), agentClientId);

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

    // An HTTP client to the in-memory API, signed in as a demo user (through that agent, if given).
    public HttpClient ClientFor(Guid userId, Guid tenantId, Guid? agentClientId = null)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", SignIn(userId, tenantId, agentClientId));
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

    // What an agent does with a pass: MCP tools/call at its engagement's address (the pass's `eng`;
    // Coke's for anything that isn't a readable pass). StatusCode is HTTP's (401 = no valid pass);
    // a refused or failed call is a 200 with IsError and the reason in Text, as MCP does it.
    public record Call(HttpStatusCode StatusCode, string Text, bool IsError);

    public async Task<Call> Invoke(string? pass, string tool, object? args = null)
    {
        var engagement = DemoSeed.Coke;
        try { engagement = Guid.Parse(new JsonWebToken(pass).GetClaim("eng").Value); } catch (Exception) { }
        var client = CreateClient();
        if (pass is not null)
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", pass);
        var res = await client.PostAsJsonAsync($"/mcp/engagements/{engagement}",
            new { jsonrpc = "2.0", id = 1, method = "tools/call", @params = new { name = tool, arguments = args ?? new { } } });
        var body = await res.Content.ReadAsStringAsync();
        if (res.StatusCode != HttpStatusCode.OK)
            return new Call(res.StatusCode, body, true);
        var result = JsonDocument.Parse(body).RootElement.GetProperty("result");
        return new Call(res.StatusCode, result.GetProperty("content")[0].GetProperty("text").GetString()!, result.GetProperty("isError").GetBoolean());
    }

    // A new agent registered for `allowedScopes`, made for one test. Used to check the layers
    // BEHIND the agent's ceiling, e.g. a careless registration that allows documents.export.
    public Guid NewAgent(string allowedScopes) => WithDb(db =>
    {
        var a = new Agent { TenantId = DemoSeed.FirmA, Name = "Overreach", AllowedScopes = allowedScopes, EntraClientId = Guid.NewGuid() };
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

    // A new Coke Staff member, linked to a new (fake) Entra account. Returns (Warden id, Entra oid).
    public (Guid user, Guid oid) NewLinkedCokeStaff()
    {
        var user = NewCokeStaff();
        var oid = Guid.NewGuid();
        WithDb(db =>
        {
            var u = db.Users.Find(user)!;
            (u.EntraTenantId, u.EntraObjectId) = (FakeEntraTenant, oid);
            return db.SaveChanges();
        });
        return (user, oid);
    }

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

    // What an agent does: POST /token/exchange with the user's sign-in token, which Entra issued to
    // that agent's app (its client id in azp; an unregistered agent id gets a client id nobody has).
    public Task<HttpResponseMessage> Exchange(Guid userId, Guid tenantId, Guid agentId, Guid engagementId, string scope)
    {
        var clientId = WithDb(db => db.Agents.Find(agentId)?.EntraClientId) ?? Guid.NewGuid();
        return ClientFor(userId, tenantId, clientId).PostAsJsonAsync("/token/exchange", new { resource = engagementId, scope });
    }
}

// Stands in for EVERY MCP server behind Warden: Tickmark's (seeded, TickmarkUrl) and a firm's
// document server (Url, added by UpstreamTests). Speaks MCP like a real one: initialize hands out a
// session id that later messages must carry, and tools/call answers as a server-sent event stream
// (text/event-stream), not plain JSON.
// Every address offers the same tools (Tickmark's three + `search` and `delete_all`): it's Warden's
// job to only use the ones assigned on THAT server. Remembers every tools/call it got.
// Down: the addresses that act like a server that's not running.
public class FakeUpstream : HttpMessageHandler
{
    public const string Url = "http://firm-a-docs.test/mcp";
    public const string Audience = "firm-a-docs";
    public const string TickmarkUrl = "http://localhost:8000/mcp";
    public List<(string Url, string Tool, string Token, string Body)> Calls { get; } = [];
    public HashSet<string> Down { get; } = [];
    public void Reset() { Calls.Clear(); Down.Clear(); }

    static readonly string[] Tools = ["list_documents", "read_document", "run_vouch", "search", "delete_all"];

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        var url = request.RequestUri!.ToString();
        if (Down.Contains(url)) throw new HttpRequestException("connection refused");
        var msg = await request.Content!.ReadFromJsonAsync<JsonElement>(ct);
        var method = msg.GetProperty("method").GetString();
        if (method == "initialize")
        {
            var init = Json(msg, new { protocolVersion = "2025-06-18", capabilities = new { tools = new { } }, serverInfo = new { name = "docs" } });
            init.Headers.Add("Mcp-Session-Id", "s-1");
            return init;
        }
        if (!request.Headers.TryGetValues("Mcp-Session-Id", out var session) || session.Single() != "s-1")
            return new HttpResponseMessage(HttpStatusCode.BadRequest);
        if (!msg.TryGetProperty("id", out _))
            return new HttpResponseMessage(HttpStatusCode.Accepted);
        if (method == "tools/list")
            return Json(msg, new { tools = Tools.Select(t => new { name = t,
                description = t == "search" ? "Search the firm's documents." : $"The {t} tool.", inputSchema = new { type = "object" } }) });

        // tools/call: answered as an event stream.
        var p = msg.GetProperty("params");
        var tool = p.GetProperty("name").GetString()!;
        Calls.Add((url, tool, request.Headers.Authorization!.Parameter!, p.GetProperty("arguments").GetRawText()));
        var reply = JsonSerializer.Serialize(new { jsonrpc = "2.0", id = msg.GetProperty("id"),
            result = new { content = new[] { new { type = "text", text = $"called {tool}" } }, isError = false } });
        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent($"event: message\ndata: {reply}\n\n", System.Text.Encoding.UTF8, "text/event-stream"),
        };
    }

    static HttpResponseMessage Json(JsonElement msg, object result) => new(HttpStatusCode.OK)
    {
        Content = JsonContent.Create(new { jsonrpc = "2.0", id = msg.GetProperty("id"), result }),
    };
}

// Stands in for Entra's token endpoint (OAuthFrontDoorController forwards there): remembers what it
// was sent and answers like Entra would.
public class FakeEntraTokens : HttpMessageHandler
{
    public (Uri Url, string Form)? Last { get; private set; }

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
    {
        Last = (request.RequestUri!, await request.Content!.ReadAsStringAsync(ct));
        return new HttpResponseMessage(HttpStatusCode.OK) { Content = JsonContent.Create(new { access_token = "from-entra", token_type = "Bearer" }) };
    }
}
