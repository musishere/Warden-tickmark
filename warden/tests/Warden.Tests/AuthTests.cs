/*
 * AuthTests.cs — step 4: Warden checks who is calling.
 *
 * Starts the API in memory (like HealthTests) and calls GET /me with different tokens:
 *   no token, wrong key, expired, missing tenant -> rejected
 *   valid token for Sara                         -> 200, tenant = Firm A, read from the token
 *
 * 401 Unauthorized = "I don't know who you are" (no/invalid token).
 * 403 Forbidden    = "I know who you are, but no" (valid token missing what's required).
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Auth;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

public class AuthTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>
{
    // The same dev key the app trusts (tests run in Development, like your laptop).
    SecurityKey DevKey => DevIssuer.SigningKey(factory.Services.GetRequiredService<IConfiguration>());

    static readonly DateTime InAnHour = DateTime.UtcNow.AddHours(1);

    async Task<HttpResponseMessage> GetMe(string? token)
    {
        var client = factory.CreateClient();
        if (token is not null)
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return await client.GetAsync("/me");
    }

    [Fact]
    public async Task request_without_token_is_rejected()
    {
        Assert.Equal(HttpStatusCode.Unauthorized, (await GetMe(null)).StatusCode);
    }

    [Fact]
    public async Task token_signed_with_wrong_key_is_rejected()
    {
        var forger = DevIssuer.SigningKey("a-key-the-attacker-made-up-themselves-32bytes+");
        var token = DevIssuer.CreateToken(forger, DemoSeed.Sara, DemoSeed.FirmA, "Sara", InAnHour);
        Assert.Equal(HttpStatusCode.Unauthorized, (await GetMe(token)).StatusCode);
    }

    [Fact]
    public async Task expired_token_is_rejected()
    {
        var token = DevIssuer.CreateToken(DevKey, DemoSeed.Sara, DemoSeed.FirmA, "Sara", DateTime.UtcNow.AddMinutes(-1));
        Assert.Equal(HttpStatusCode.Unauthorized, (await GetMe(token)).StatusCode);
    }

    [Fact]
    public async Task token_without_tenant_is_rejected()
    {
        var token = DevIssuer.CreateToken(DevKey, DemoSeed.Sara, tenantId: null, "Sara", InAnHour);
        Assert.Equal(HttpStatusCode.Forbidden, (await GetMe(token)).StatusCode);
    }

    [Fact]
    public async Task tenant_comes_from_verified_token()
    {
        var token = DevIssuer.CreateToken(DevKey, DemoSeed.Sara, DemoSeed.FirmA, "Sara", InAnHour);
        var res = await GetMe(token);

        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        var me = await res.Content.ReadFromJsonAsync<Me>();
        Assert.Equal(DemoSeed.Sara.ToString(), me!.UserId);
        Assert.Equal(DemoSeed.FirmA.ToString(), me.TenantId);
    }

    // Shape of the /me JSON. A "record" is a small data class (like a Python dataclass).
    record Me(string UserId, string TenantId, string? Name);
}
