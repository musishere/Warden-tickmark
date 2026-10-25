/*
 * RevokeTests.cs — step 13: POST /token/revoke and the SCIM endpoint's guard rails.
 *
 *   Sara revokes her own pass            -> 200; the gate refuses it from then on
 *   Ali tries to revoke Sara's pass      -> 404 (same as "doesn't exist")
 *   SCIM with no / a wrong secret        -> 401
 *   Firm A's SCIM secret on Dana (Firm B) -> 404: a firm's secret only reaches its own users
 *   SCIM asking to change the email      -> 400 (only "active" is synced)
 * (Deactivation stopping a working agent: SecurityGuaranteeTests.scim_deactivation_revokes_agent_tokens.)
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class RevokeTests(WardenFactory warden)
{
    [Fact]
    public async Task user_revokes_their_own_pass_and_the_gate_refuses_it()
    {
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");
        var jti = new JsonWebToken(pass).Id;

        var res = await warden.ClientFor(DemoSeed.Sara, DemoSeed.FirmA).PostAsJsonAsync("/token/revoke", new { jti });
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);

        var call = await warden.Invoke(pass, "read_document");
        Assert.Equal(HttpStatusCode.Unauthorized, call.StatusCode);
        Assert.Contains("pass revoked", call.Text);
    }

    [Fact]
    public async Task nobody_else_can_revoke_your_pass()
    {
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");
        var jti = new JsonWebToken(pass).Id;

        var res = await warden.ClientFor(DemoSeed.Ali, DemoSeed.FirmA).PostAsJsonAsync("/token/revoke", new { jti });
        Assert.Equal(HttpStatusCode.NotFound, res.StatusCode);
        Assert.True((await warden.Check(pass)).Ok);  // still works
    }

    [Fact]
    public async Task scim_needs_the_firms_secret_and_reaches_only_its_users()
    {
        var leaver = warden.NewCokeStaff();
        Assert.Equal(HttpStatusCode.Unauthorized, (await warden.ScimDeactivate(leaver, secret: "wrong")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await warden.ScimDeactivate(DemoSeed.Dana)).StatusCode);
        Assert.True(warden.WithDb(db => db.Users.Find(leaver)!.Active));
        Assert.True(warden.WithDb(db => db.Users.Find(DemoSeed.Dana)!.Active));
    }

    [Fact]
    public async Task scim_only_changes_active()
    {
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", WardenFactory.ScimSecret);
        var res = await client.PatchAsJsonAsync($"/scim/v2/Users/{warden.NewCokeStaff()}", new
        {
            Operations = new[] { new { op = "replace", path = "userName", value = "evil@x.test" } },
        });
        Assert.Equal(HttpStatusCode.BadRequest, res.StatusCode);
    }
}
