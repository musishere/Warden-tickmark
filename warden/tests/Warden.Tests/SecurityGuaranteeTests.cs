/*
 * SecurityGuaranteeTests.cs — the security promises Warden makes, one test each.
 *
 * Each test name is a guarantee in plain English. Skipped ones say which MVP.md step
 * switches them on. If one of these ever fails, Warden has broken a promise.
 */
using System.Net;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class SecurityGuaranteeTests(WardenFactory warden)
{
    // Sara is Staff on Coke. Staff may not export, so her agent can't get a pass that exports.
    [Fact]
    public async Task agent_cannot_exceed_user_permissions()
    {
        var res = await warden.Exchange(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Tickmark, DemoSeed.Coke,
            "documents.read documents.export");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("documents.export", await res.Content.ReadAsStringAsync());
    }

    [Fact(Skip = "step 7")] public void ethical_wall_blocks_even_admin() { }
    [Fact(Skip = "step 13")] public void scim_deactivation_revokes_agent_tokens() { }
    // A real Warden pass, in the logbook, not revoked, but expired a minute ago: refused.
    [Fact]
    public async Task expired_delegated_token_is_rejected()
    {
        var pass = warden.Stamp(WardenFactory.SaraOnCoke(expires: DateTime.UtcNow.AddMinutes(-1)));
        var check = await warden.Check(pass);
        Assert.False(check.Ok);
        Assert.Equal("pass expired", check.Reason);
    }

    [Fact(Skip = "later: credits")] public void concurrent_requests_cannot_overspend_credits() { }
    [Fact(Skip = "step 10")] public void prompt_injection_cannot_trigger_export() { }
    [Fact(Skip = "later: human approval")] public void override_requires_human_approval() { }
    [Fact(Skip = "step 7")] public void tenant_a_cannot_read_tenant_b() { }
}
