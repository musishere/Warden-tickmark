/*
 * McpSignInTests.cs — steps 17 + 17a: a stock MCP client signs in at Microsoft (the fake Entra here)
 * and Warden does the rest. The agent never asks for, or sees, a pass.
 *
 *   no token                          -> 401 + WWW-Authenticate: Bearer resource_metadata="..."
 *                                        -> that document names Entra and the scope to ask for
 *   Entra sign-in (azp = Tickmark)    -> works: Warden makes ONE pass for the session, reuses it,
 *                                        and its scopes are Tickmark's ceiling ∩ the user's role
 *   that pass revoked                 -> the same sign-in is refused from then on
 *   a dev sign-in at a walled address -> 403 "ethical wall", logged
 *   a sign-in issued to an app no admin registered -> refused, at /mcp AND /token/exchange,
 *                                        whatever agent id the body claims
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class McpSignInTests : IDisposable
{
    readonly WardenFactory warden;

    public McpSignInTests(WardenFactory warden)
    {
        this.warden = warden;
        warden.Upstream.Reset();
    }

    public void Dispose() => warden.Upstream.Reset();

    Task<HttpResponseMessage> Mcp(string? token, Guid engagement, string method, object? @params = null)
    {
        var client = warden.CreateClient();
        if (token is not null)
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client.PostAsJsonAsync($"/mcp/engagements/{engagement}", new { jsonrpc = "2.0", id = 1, method, @params = @params ?? new { } });
    }

    static async Task<JsonElement> Result(HttpResponseMessage res)
    {
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("result");
    }

    [Fact]
    public async Task mcp_client_without_sign_in_gets_401_with_where_to_sign_in()
    {
        var res = await Mcp(null, DemoSeed.Coke, "initialize");
        Assert.Equal(HttpStatusCode.Unauthorized, res.StatusCode);
        var where = $"http://localhost/.well-known/oauth-protected-resource/mcp/engagements/{DemoSeed.Coke}";
        Assert.Equal($"Bearer resource_metadata=\"{where}\"", res.Headers.WwwAuthenticate.ToString());

        var meta = await warden.CreateClient().GetFromJsonAsync<JsonElement>(where);
        Assert.Equal($"http://localhost/mcp/engagements/{DemoSeed.Coke}", meta.GetProperty("resource").GetString());
        Assert.Equal($"https://login.microsoftonline.com/{WardenFactory.FakeEntraTenant}/v2.0", meta.GetProperty("authorization_servers")[0].GetString());
        Assert.Equal($"api://{WardenFactory.FakeEntraClient}/access_as_user", meta.GetProperty("scopes_supported")[0].GetString());
    }

    [Fact]
    public async Task signed_in_agent_works_without_ever_seeing_a_pass()
    {
        var (user, oid) = warden.NewLinkedCokeStaff();
        var signIn = WardenFactory.EntraToken(oid);

        Assert.Equal(HttpStatusCode.OK, (await Mcp(signIn, DemoSeed.Coke, "initialize")).StatusCode);
        var tools = (await Result(await Mcp(signIn, DemoSeed.Coke, "tools/list"))).GetProperty("tools")
            .EnumerateArray().Select(t => t.GetProperty("name").GetString()).ToList();
        Assert.Contains("run_vouch", tools);
        var ok = await Result(await Mcp(signIn, DemoSeed.Coke, "tools/call", new { name = "list_documents", arguments = new { } }));
        Assert.False(ok.GetProperty("isError").GetBoolean());
        Assert.Equal("list_documents", warden.Upstream.Calls.Single().Tool);

        // Three messages, ONE pass, made by Warden: Tickmark's ceiling ∩ Staff.
        var pass = warden.WithDb(db => db.DelegatedTokens.Single(t => t.UserId == user));
        Assert.Equal((DemoSeed.Tickmark, "documents.read vouch.run"), (pass.AgentId, pass.Scope));

        // Revoked: this sign-in is done, it doesn't quietly get a new pass.
        warden.WithDb(db => { db.DelegatedTokens.Find(pass.Jti)!.RevokedAt = DateTime.UtcNow; return db.SaveChanges(); });
        Assert.Equal(HttpStatusCode.Unauthorized, (await Mcp(signIn, DemoSeed.Coke, "tools/call", new { name = "list_documents" })).StatusCode);
        Assert.Single(warden.Upstream.Calls);
    }

    [Fact]
    public async Task sign_in_cannot_open_a_walled_engagement()
    {
        var sara = warden.SignIn(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.TickmarkClientId);  // a dev sign-in works the same way
        var res = await Mcp(sara, DemoSeed.Pepsi, "tools/list");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("ethical wall", await res.Content.ReadAsStringAsync());
        Assert.False(warden.WithDb(db => db.AuditLog.OrderByDescending(a => a.Id)
            .First(a => a.UserId == DemoSeed.Sara && a.EngagementId == DemoSeed.Pepsi)).Allowed);
    }

    [Fact]
    public async Task unknown_agent_is_refused_even_with_a_valid_sign_in()
    {
        var (_, oid) = warden.NewLinkedCokeStaff();
        var stranger = WardenFactory.EntraToken(oid, azp: Guid.NewGuid());  // a real sign-in, to an app nobody registered

        var res = await Mcp(stranger, DemoSeed.Coke, "tools/list");
        Assert.Equal(HttpStatusCode.Forbidden, res.StatusCode);
        Assert.Contains("unknown agent", await res.Content.ReadAsStringAsync());

        // Claiming to be Tickmark in the body doesn't help: the agent is the token's azp.
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", stranger);
        var exchange = await client.PostAsJsonAsync("/token/exchange", new { agent_id = DemoSeed.Tickmark, resource = DemoSeed.Coke, scope = "documents.read" });
        Assert.Equal(HttpStatusCode.Forbidden, exchange.StatusCode);
        Assert.Contains("unknown agent", await exchange.Content.ReadAsStringAsync());
        Assert.Empty(warden.Upstream.Calls);
    }
}
