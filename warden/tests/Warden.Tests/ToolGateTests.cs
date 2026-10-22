/*
 * ToolGateTests.cs — steps 9-10: POST /tools/{tool}/invoke, with a fake tool server (FakeTools).
 *
 *   Sara's pass (documents.read) -> read_document on Coke   -> forwarded with a 60-second token
 *                                                             for tickmark-tools, args untouched; logged
 *   no pass / garbage / a sign-in token instead of a pass    -> 401, nothing forwarded
 *   same pass -> run_vouch (scope not on the pass)           -> 403, nothing forwarded, logged
 *   revoked pass                                             -> 401 "pass revoked", logged
 *   tool server down                                         -> 502, not a crash
 * (Agents can't export: SecurityGuaranteeTests.prompt_injection_cannot_trigger_export.)
 */
using System.Net;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class ToolGateTests : IDisposable
{
    readonly WardenFactory warden;

    public ToolGateTests(WardenFactory warden)
    {
        this.warden = warden;
        warden.Tools.Reset();  // each test starts with nothing forwarded
    }

    public void Dispose() => warden.Tools.Reset();

    Task<string> SaraReadsCoke() => warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");

    [Fact]
    public async Task allowed_call_is_forwarded_with_a_short_tool_token_and_logged()
    {
        var pass = await SaraReadsCoke();
        var res = await warden.Invoke(pass, "read_document", new { name = "inv1.pdf" });

        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        Assert.Equal("/tools/read_document", warden.Tools.Last!.RequestUri!.AbsolutePath);
        Assert.Equal("""{"name":"inv1.pdf"}""", warden.Tools.LastBody);

        // What Tickmark receives: NOT the agent's pass, a separate one-minute token for one tool.
        var sent = new JsonWebToken(warden.Tools.Last.Headers.Authorization!.Parameter);
        Assert.NotEqual(pass, sent.EncodedToken);
        Assert.Equal("tickmark-tools", sent.Audiences.Single());
        Assert.Equal("read_document", sent.GetClaim("tool").Value);
        Assert.Equal(DemoSeed.Coke.ToString(), sent.GetClaim("eng").Value);
        Assert.True(sent.ValidTo <= DateTime.UtcNow.AddSeconds(61));

        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        var row = warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "read_document"));
        Assert.True(row.Allowed);
    }

    [Fact]
    public async Task missing_or_fake_pass_is_401_and_nothing_is_forwarded()
    {
        Assert.Equal(HttpStatusCode.Unauthorized, (await warden.Invoke(null, "read_document")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await warden.Invoke("not.a.pass", "read_document")).StatusCode);
        var signIn = warden.SignIn(DemoSeed.Sara, DemoSeed.FirmA);  // a sign-in token is not a pass
        Assert.Equal(HttpStatusCode.Unauthorized, (await warden.Invoke(signIn, "read_document")).StatusCode);
        Assert.Null(warden.Tools.Last);
    }

    [Fact]
    public async Task scope_not_on_the_pass_is_403_and_logged()
    {
        var pass = await SaraReadsCoke();
        var res = await warden.Invoke(pass, "run_vouch");

        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("pass does not include vouch.run", await res.Content.ReadAsStringAsync());
        Assert.Null(warden.Tools.Last);
        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        Assert.False(warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "run_vouch")).Allowed);
    }

    [Fact]
    public async Task revoked_pass_is_refused_and_logged()
    {
        var pass = await SaraReadsCoke();
        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        warden.WithDb(db => { db.DelegatedTokens.Find(jti)!.RevokedAt = DateTime.UtcNow; return db.SaveChanges(); });

        var res = await warden.Invoke(pass, "read_document");

        Assert.Equal(HttpStatusCode.Unauthorized, res.StatusCode);
        Assert.Null(warden.Tools.Last);
        Assert.Equal("pass revoked", warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "read_document")).Reason);
    }

    [Fact]
    public async Task tool_server_down_is_a_502()
    {
        var pass = await SaraReadsCoke();
        warden.Tools.Down = true;
        Assert.Equal(HttpStatusCode.BadGateway, (await warden.Invoke(pass, "read_document")).StatusCode);
    }
}
