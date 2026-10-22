/*
 * SigningKeyTests.cs — step 5: Warden's own stamp + published public key (JWKS).
 *
 * These tests play the part of Tickmark:
 *   1. download GET /.well-known/jwks.json without any token (it's public)
 *   2. check it contains only the PUBLIC key (no private numbers d, p, q, ...)
 *   3. a pass signed by Warden checks out against the downloaded key
 *   4. a pass signed by some other key (a forger) does NOT check out
 */
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Auth;
using Xunit;

namespace Warden.Tests;

public class SigningKeyTests(WebApplicationFactory<Program> factory) : IClassFixture<WebApplicationFactory<Program>>
{
    static readonly string[] PrivateFields = ["d", "p", "q", "dp", "dq", "qi"];

    async Task<string> DownloadJwks() => await factory.CreateClient().GetStringAsync("/.well-known/jwks.json");

    static string Sign(SigningCredentials creds) => new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
    {
        Issuer = "warden",
        Claims = new Dictionary<string, object> { ["sub"] = "sara" },
        Expires = DateTime.UtcNow.AddMinutes(15),
        SigningCredentials = creds,
    });

    // What Tickmark will do: check a pass using only the downloaded public keys.
    static async Task<bool> CheckWithJwks(string token, string jwksJson)
    {
        var result = await new JsonWebTokenHandler().ValidateTokenAsync(token, new TokenValidationParameters
        {
            ValidIssuer = "warden",
            ValidateAudience = false,  // audiences come in step 6
            IssuerSigningKeys = new JsonWebKeySet(jwksJson).GetSigningKeys(),
        });
        return result.IsValid;
    }

    [Fact]
    public async Task jwks_is_public_and_contains_no_private_key()
    {
        var json = await DownloadJwks();  // no token sent: must still work
        var key = JsonDocument.Parse(json).RootElement.GetProperty("keys")[0];

        Assert.Equal("RSA", key.GetProperty("kty").GetString());
        Assert.False(string.IsNullOrEmpty(key.GetProperty("kid").GetString()));
        foreach (var field in PrivateFields)
            Assert.False(key.TryGetProperty(field, out _), $"JWKS leaked private field '{field}'");
    }

    [Fact]
    public async Task pass_signed_by_warden_checks_out_with_published_key()
    {
        var warden = factory.Services.GetRequiredService<WardenSigningKey>();
        Assert.True(await CheckWithJwks(Sign(warden.Credentials), await DownloadJwks()));
    }

    [Fact]
    public async Task pass_signed_by_another_key_is_rejected()
    {
        var forger = new SigningCredentials(new RsaSecurityKey(RSA.Create(2048)), SecurityAlgorithms.RsaSha256);
        Assert.False(await CheckWithJwks(Sign(forger), await DownloadJwks()));
    }
}
