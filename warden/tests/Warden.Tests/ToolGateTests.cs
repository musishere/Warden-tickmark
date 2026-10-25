/*
 * ToolGateTests.cs — steps 9-10: the gate, reached through MCP tools/call (the only door since
 * step 19), with fake MCP servers (FakeUpstream).
 *
 *   Sara's pass (documents.read) -> read_document on Coke   -> sent over MCP to Firm A's "tickmark"
 *                                                             server (step 18b) with a 60-second token
 *                                                             for tickmark-tools, args untouched; logged
 *   no pass / garbage                                       -> 401, nothing forwarded
 *   a sign-in token issued to no registered agent           -> refused, nothing forwarded
 *   same pass -> run_vouch (scope not on the pass)           -> refused (isError), nothing forwarded, logged
 *   revoked pass                                             -> 401 "pass revoked", logged
 *   tool server down                                         -> an error result (tool_unavailable), not a crash
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
        warden.Upstream.Reset();  // each test starts with nothing forwarded
    }

    public void Dispose() => warden.Upstream.Reset();

    Task<string> SaraReadsCoke() => warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");

    [Fact]
    public async Task tickmark_tools_work_as_an_upstream_mcp_server()
    {
        var pass = await SaraReadsCoke();
        var res = await warden.Invoke(pass, "read_document", new { name = "inv1.pdf" });

        // The tool's answer (the text of its MCP result), passed on as-is.
        Assert.False(res.IsError);
        Assert.Equal("called read_document", res.Text);

        // Sent as MCP tools/call to the seeded server, nowhere else, with the arguments untouched.
        var (url, tool, token, body) = warden.Upstream.Calls.Single();
        Assert.Equal((FakeUpstream.TickmarkUrl, "read_document"), (url, tool));
        Assert.Equal("""{"name":"inv1.pdf"}""", body);

        // What Tickmark receives: NOT the agent's pass, a separate one-minute token for one tool.
        var sent = new JsonWebToken(token);
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
        var signIn = warden.SignIn(DemoSeed.Sara, DemoSeed.FirmA);  // a sign-in, but by no registered agent
        Assert.Contains("unknown agent", (await warden.Invoke(signIn, "read_document")).Text);
        Assert.Empty(warden.Upstream.Calls);
    }

    [Fact]
    public async Task scope_not_on_the_pass_is_403_and_logged()
    {
        var pass = await SaraReadsCoke();
        var res = await warden.Invoke(pass, "run_vouch");

        Assert.True(res.IsError);
        Assert.Contains("pass does not include vouch.run", res.Text);
        Assert.Empty(warden.Upstream.Calls);
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
        Assert.Empty(warden.Upstream.Calls);
        Assert.Equal("pass revoked", warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "read_document")).Reason);
    }

    [Fact]
    public async Task tool_server_down_is_an_error_not_a_crash()
    {
        var pass = await SaraReadsCoke();
        warden.Upstream.Down.Add(FakeUpstream.TickmarkUrl);
        var res = await warden.Invoke(pass, "read_document");
        Assert.True(res.IsError);
        Assert.Contains("tool_unavailable", res.Text);
    }
}
