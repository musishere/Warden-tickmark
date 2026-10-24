/*
 * UpstreamTests.cs — step 18a: Warden in front of a firm's own MCP server (FakeUpstream).
 *
 * Firm A has connected one server, "docs", offering `search` and `delete_all`.
 * The admin assigned `search` → documents.read, and nothing for `delete_all`.
 *
 *   tools/list                    -> search (described by the server itself), no delete_all
 *   tools/call delete_all         -> refused as an unknown tool, never sent to the server, logged
 *   tools/call search             -> gate -> sent over MCP with a one-minute token for "firm-a-docs";
 *                                    the server's answer comes back as-is; logged
 *   admin assigns delete_all      -> listed from the next tools/list on
 *   Firm B's agent                -> sees none of Firm A's server tools
 *   server down                   -> tools/list still works (that server is skipped); a call is an error
 *   two firms, same audience      -> refused by the database: a token for one server can't open another
 */
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Warden.Core.Domain;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class UpstreamTests : IDisposable
{
    static readonly Guid Docs = Guid.Parse("d0c50000-0000-0000-0000-000000000001");
    readonly WardenFactory warden;

    public UpstreamTests(WardenFactory warden)
    {
        this.warden = warden;
        warden.Upstream.Reset();
        // Firm A's server + the admin's one assignment, exactly, whatever earlier runs left behind.
        warden.WithDb(db =>
        {
            if (db.UpstreamServers.Find(Docs) is null)
                db.UpstreamServers.Add(new UpstreamServer { Id = Docs, TenantId = DemoSeed.FirmA, Name = "docs", Url = FakeUpstream.Url, Audience = FakeUpstream.Audience });
            db.UpstreamTools.RemoveRange(db.UpstreamTools.Where(t => t.ServerId == Docs));
            db.SaveChanges();
            db.UpstreamTools.Add(new UpstreamTool { TenantId = DemoSeed.FirmA, Name = "search", ServerId = Docs, Scope = "documents.read" });
            return db.SaveChanges();
        });
    }

    public void Dispose() => warden.Upstream.Reset();

    async Task<JsonElement> Mcp(string pass, Guid engagement, string method, object? @params = null)
    {
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", pass);
        var res = await client.PostAsJsonAsync($"/mcp/engagements/{engagement}", new { jsonrpc = "2.0", id = 1, method, @params = @params ?? new { } });
        res.EnsureSuccessStatusCode();
        return (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("result");
    }

    async Task<List<string>> ToolNames(string pass, Guid engagement) =>
        (await Mcp(pass, engagement, "tools/list")).GetProperty("tools").EnumerateArray().Select(t => t.GetProperty("name").GetString()!).ToList();

    Task<string> SaraReadsCoke() => warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");

    [Fact]
    public async Task upstream_tool_is_hidden_until_an_admin_assigns_a_scope()
    {
        var pass = await SaraReadsCoke();

        // The menu: the server's `search`, with the server's own description. No delete_all.
        var list = await Mcp(pass, DemoSeed.Coke, "tools/list");
        var search = list.GetProperty("tools").EnumerateArray().Single(t => t.GetProperty("name").GetString() == "search");
        Assert.Equal("Search the firm's documents.", search.GetProperty("description").GetString());
        Assert.DoesNotContain("delete_all", await ToolNames(pass, DemoSeed.Coke));

        // Calling it anyway: refused, never sent to the server, logged.
        var no = await Mcp(pass, DemoSeed.Coke, "tools/call", new { name = "delete_all" });
        Assert.True(no.GetProperty("isError").GetBoolean());
        Assert.Contains("unknown tool: delete_all", no.GetProperty("content")[0].GetProperty("text").GetString());
        Assert.Empty(warden.Upstream.Calls);
        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        Assert.False(warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "delete_all")).Allowed);

        // The admin assigns it a scope: from the next tools/list on, it's there.
        warden.WithDb(db =>
        {
            db.UpstreamTools.Add(new UpstreamTool { TenantId = DemoSeed.FirmA, Name = "delete_all", ServerId = Docs, Scope = "documents.read" });
            return db.SaveChanges();
        });
        Assert.Contains("delete_all", await ToolNames(pass, DemoSeed.Coke));
    }

    [Fact]
    public async Task allowed_upstream_call_goes_through_the_gate_and_comes_back_as_is()
    {
        var pass = await SaraReadsCoke();
        var ok = await Mcp(pass, DemoSeed.Coke, "tools/call", new { name = "search", arguments = new { q = "invoice 42" } });

        Assert.False(ok.GetProperty("isError").GetBoolean());
        Assert.Equal("called search", ok.GetProperty("content")[0].GetProperty("text").GetString());

        // What the server received: its own one-minute token from Warden, the arguments untouched.
        var (url, tool, token, body) = warden.Upstream.Calls.Single();
        Assert.Equal((FakeUpstream.Url, "search"), (url, tool));
        Assert.Equal("""{"q":"invoice 42"}""", body);
        var sent = new JsonWebToken(token);
        Assert.NotEqual(pass, token);
        Assert.Equal(FakeUpstream.Audience, sent.Audiences.Single());
        Assert.Equal(DemoSeed.Coke.ToString(), sent.GetClaim("eng").Value);
        Assert.True(sent.ValidTo <= DateTime.UtcNow.AddSeconds(61));

        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        Assert.True(warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == "search")).Allowed);
    }

    [Fact]
    public async Task another_firm_sees_none_of_firm_a_servers()
    {
        var dana = await warden.Pass(DemoSeed.Dana, DemoSeed.FirmB, DemoSeed.Acme, "documents.read");
        Assert.DoesNotContain("search", await ToolNames(dana, DemoSeed.Acme));
        var no = await Mcp(dana, DemoSeed.Acme, "tools/call", new { name = "search" });
        Assert.True(no.GetProperty("isError").GetBoolean());
        Assert.Empty(warden.Upstream.Calls);
    }

    [Fact]
    public async Task server_down_skips_it_in_the_list_and_fails_the_call()
    {
        var pass = await SaraReadsCoke();
        warden.Upstream.Down.Add(FakeUpstream.Url);

        var names = await ToolNames(pass, DemoSeed.Coke);
        Assert.Contains("read_document", names);  // Tickmark's server still answers: the rest of the menu works
        Assert.DoesNotContain("search", names);
        Assert.True((await Mcp(pass, DemoSeed.Coke, "tools/call", new { name = "search" })).GetProperty("isError").GetBoolean());
    }

    [Fact]
    public void two_servers_cannot_share_an_audience() =>
        Assert.Throws<Microsoft.EntityFrameworkCore.DbUpdateException>(() => warden.WithDb(db =>
        {
            db.UpstreamServers.Add(new UpstreamServer { TenantId = DemoSeed.FirmB, Name = "copycat", Url = "http://b.test/mcp", Audience = "tickmark-tools" });
            return db.SaveChanges();
        }));
}
