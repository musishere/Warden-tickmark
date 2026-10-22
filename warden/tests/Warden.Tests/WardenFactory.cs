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
 * xUnit runs test classes in PARALLEL by default. Every class that touches the database
 * joins the "database" collection ([Collection(WardenFactory.Db)]): they share ONE factory
 * (seeded once) and run one after another, so they can't trip over each other.
 */
using System.Net.Http.Headers;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Warden.Api.Auth;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[CollectionDefinition(WardenFactory.Db)]
public class DatabaseCollection : ICollectionFixture<WardenFactory>;

public class WardenFactory : WebApplicationFactory<Program>
{
    public const string Db = "database";
    public const string TestDb = "Host=localhost;Port=5433;Database=warden_test;Username=warden;Password=warden";

    protected override void ConfigureWebHost(IWebHostBuilder builder) =>
        builder.UseSetting("ConnectionStrings:Warden", TestDb);

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

    // What an agent does: POST /token/exchange with the user's sign-in token.
    public Task<HttpResponseMessage> Exchange(Guid userId, Guid tenantId, Guid agentId, Guid engagementId, string scope)
    {
        var client = CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", SignIn(userId, tenantId));
        return client.PostAsJsonAsync("/token/exchange", new { agent_id = agentId, resource = engagementId, scope });
    }
}
