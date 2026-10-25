/*
 * PassCheckTests.cs — step 6c: AgentPass.Check, what the gate will run on every tool call.
 *
 *   a pass from /token/exchange                 -> OK, returns the logbook row
 *   one character changed                       -> bad signature
 *   signed by some other key                    -> bad signature
 *   Sara's sign-in token used as if it's a pass -> refused (not stamped by Warden's key)
 *   validly stamped, but not in the logbook     -> refused
 *   in the logbook but revoked                  -> refused
 * (An expired pass is in SecurityGuaranteeTests.)
 */
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class PassCheckTests(WardenFactory warden)
{
    static readonly DateTime InTenMinutes = DateTime.UtcNow.AddMinutes(10);

    async Task<string> PassFromExchange()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");
        return (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("access_token").GetString()!;
    }

    [Fact]
    public async Task pass_from_exchange_is_accepted()
    {
        var check = await warden.Check(await PassFromExchange());
        Assert.True(check.Ok, check.Reason);
        Assert.Equal(DemoSeed.Sara, check.Row!.UserId);
        Assert.Equal(DemoSeed.Coke, check.Row.EngagementId);
    }

    [Fact]
    public async Task tampered_pass_is_rejected()
    {
        var pass = await PassFromExchange();
        var tampered = pass[..^2] + (pass[^2] == 'A' ? 'B' : 'A') + pass[^1];  // change one signature character
        var check = await warden.Check(tampered);
        Assert.False(check.Ok);
        Assert.Equal("bad signature", check.Reason);
    }

    [Fact]
    public async Task pass_signed_by_another_key_is_rejected()
    {
        var forged = new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
        {
            Issuer = "warden", Audience = "warden-tools", Expires = InTenMinutes,
            Claims = new Dictionary<string, object> { ["jti"] = Guid.NewGuid().ToString() },
            SigningCredentials = new SigningCredentials(new RsaSecurityKey(RSA.Create(2048)), SecurityAlgorithms.RsaSha256),
        });
        var check = await warden.Check(forged);
        Assert.False(check.Ok);
        Assert.Equal("bad signature", check.Reason);
    }

    [Fact]
    public async Task sign_in_token_is_not_a_pass()
    {
        var check = await warden.Check(warden.SignIn(DemoSeed.Sara, DemoSeed.FirmA));
        Assert.False(check.Ok);
    }

    [Fact]
    public async Task pass_missing_from_logbook_is_rejected()
    {
        var pass = warden.Stamp(WardenFactory.SaraOnCoke(InTenMinutes), saveToLogbook: false);
        var check = await warden.Check(pass);
        Assert.False(check.Ok);
        Assert.Equal("pass not in logbook", check.Reason);
    }

    [Fact]
    public async Task revoked_pass_is_rejected()
    {
        var row = WardenFactory.SaraOnCoke(InTenMinutes);
        row.RevokedAt = DateTime.UtcNow;
        var check = await warden.Check(warden.Stamp(row));
        Assert.False(check.Ok);
        Assert.Equal("pass revoked", check.Reason);
    }
}
