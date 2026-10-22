/*
 * TokenExchangeTests.cs — step 6b: POST /token/exchange, using the demo world (DemoSeed.cs).
 *
 *   Sara (Staff, Coke) asks Tickmark to read Coke  -> 200, a narrow 15-minute pass, logged
 *   Sara asks for Pepsi (she's walled off)         -> 403 "ethical wall"
 *   Dana (Firm B) asks for Coke (Firm A)           -> 403, looks the same as "doesn't exist"
 *   an unregistered agent                          -> 403
 *   a made-up scope                                -> 400
 * (Sara asking for export, beyond her role, is in SecurityGuaranteeTests.)
 */
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class TokenExchangeTests(WardenFactory warden)
{
    [Fact]
    public async Task sara_gets_a_narrow_pass_for_coke()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke, "vouch.run documents.read");
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);

        var body = await res.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(900, body.GetProperty("expires_in").GetInt32());  // 15 minutes

        // Read what's inside the pass (no checking here, that's 6c; just looking).
        var pass = new JsonWebToken(body.GetProperty("access_token").GetString());
        Assert.Equal("warden", pass.Issuer);
        Assert.Equal(DemoSeed.Sara.ToString(), pass.Subject);                       // on whose behalf
        Assert.Contains(DemoSeed.Tickmark.ToString(), pass.GetClaim("act").Value);  // who acts
        Assert.Equal(DemoSeed.Coke.ToString(), pass.GetClaim("eng").Value);         // only Coke
        Assert.Equal("documents.read vouch.run", pass.GetClaim("scope").Value);     // only these

        // The pass is in the logbook under its jti.
        using var scope = warden.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
        var row = db.DelegatedTokens.Find(Guid.Parse(pass.Id));
        Assert.NotNull(row);
        Assert.Equal(DemoSeed.Tickmark, row.AgentId);
    }

    [Fact]
    public async Task walled_user_gets_no_pass()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Pepsi, "documents.read");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("ethical wall", await res.Content.ReadAsStringAsync());
    }

    [Fact]
    public async Task other_firms_engagement_gets_no_pass()
    {
        var res = await warden.Exchange(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Tickmark, DemoSeed.Coke, "documents.read");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("not found in your firm", await res.Content.ReadAsStringAsync());
    }

    [Fact]
    public async Task unregistered_agent_gets_no_pass()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, Guid.NewGuid(), DemoSeed.Coke, "documents.read");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
    }

    [Fact]
    public async Task unknown_scope_is_a_bad_request()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke, "documents.delete");
        Assert.Equal(HttpStatusCode.BadRequest, res.StatusCode);
    }
}
