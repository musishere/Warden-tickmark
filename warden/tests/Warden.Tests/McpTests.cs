/*
 * McpTests.cs — step 16: Warden speaks MCP at /mcp/engagements/{id}, with the fake tool server.
 *
 *   initialize                         -> protocol version + "I offer tools"
 *   tools/list                         -> only the tools this pass may call (export never listed)
 *   tools/call, allowed                -> forwarded through the same gate, logged
 *   tools/call, refused                -> MCP result with isError + the reason, nothing forwarded, logged
 *   no pass                            -> HTTP 401
 *   pass for Coke, URL for Pepsi       -> refused, nothing forwarded, logged
 */
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.IdentityModel.JsonWebTokens;
using Warden.Api.Data;
using Xunit;

namespace Warden.Tests;

[Collection(WardenFactory.Db)]
public class McpTests : IDisposable
{
    readonly WardenFactory warden;

    public McpTests(WardenFactory warden)
    {
        this.warden = warden;
        warden.Tools.Reset();
    }

    public void Dispose() => warden.Tools.Reset();

    // What an MCP client sends: one JSON-RPC message, POSTed to the engagement's MCP address.
    Task<HttpResponseMessage> Mcp(string? pass, Guid engagement, string method, object? @params = null)
    {
        var client = warden.CreateClient();
        if (pass is not null)
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", pass);
        return client.PostAsJsonAsync($"/mcp/engagements/{engagement}",
            new { jsonrpc = "2.0", id = 1, method, @params = @params ?? new { } });
    }

    static async Task<JsonElement> Result(HttpResponseMessage res)
    {
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        return (await res.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("result");
    }

    // The audit log row for this pass + tool (fails the test if there isn't exactly one).
    Warden.Core.Domain.AuditEntry Audited(string pass, string tool)
    {
        var jti = Guid.Parse(new JsonWebToken(pass).Id);
        return warden.WithDb(db => db.AuditLog.Single(a => a.Jti == jti && a.Action == tool));
    }

    [Fact]
    public async Task mcp_call_is_checked_and_logged_like_any_tool_call()
    {
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");

        var init = await Result(await Mcp(pass, DemoSeed.Coke, "initialize", new { protocolVersion = "2025-06-18" }));
        Assert.Equal("2025-06-18", init.GetProperty("protocolVersion").GetString());
        Assert.True(init.GetProperty("capabilities").TryGetProperty("tools", out _));

        // Only what this pass may do: documents.read tools. No run_vouch (not on the pass), no export (humans only).
        var tools = (await Result(await Mcp(pass, DemoSeed.Coke, "tools/list"))).GetProperty("tools")
            .EnumerateArray().Select(t => t.GetProperty("name").GetString()).ToList();
        Assert.Contains("list_documents", tools);
        Assert.Contains("read_document", tools);
        Assert.DoesNotContain("run_vouch", tools);
        Assert.DoesNotContain("export_documents", tools);

        // Allowed: forwarded with the arguments untouched, the tool's answer comes back as text.
        var ok = await Result(await Mcp(pass, DemoSeed.Coke, "tools/call", new { name = "read_document", arguments = new { name = "inv1.pdf" } }));
        Assert.False(ok.GetProperty("isError").GetBoolean());
        Assert.Contains("\"ok\":true", ok.GetProperty("content")[0].GetProperty("text").GetString());
        Assert.Equal("""{"name":"inv1.pdf"}""", warden.Tools.LastBody);
        Assert.True(Audited(pass, "read_document").Allowed);

        // Refused: the agent is told why, nothing is forwarded, and it's in the audit log.
        warden.Tools.Reset();
        var no = await Result(await Mcp(pass, DemoSeed.Coke, "tools/call", new { name = "export_documents" }));
        Assert.True(no.GetProperty("isError").GetBoolean());
        Assert.Contains("access_denied", no.GetProperty("content")[0].GetProperty("text").GetString());
        Assert.Null(warden.Tools.Last);
        Assert.False(Audited(pass, "export_documents").Allowed);
    }

    [Fact]
    public async Task no_pass_is_401()
    {
        Assert.Equal(HttpStatusCode.Unauthorized, (await Mcp(null, DemoSeed.Coke, "initialize")).StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, (await Mcp(null, DemoSeed.Coke, "tools/call", new { name = "read_document" })).StatusCode);
        Assert.Null(warden.Tools.Last);
    }

    [Fact]
    public async Task pass_for_coke_cannot_be_used_at_pepsis_address()
    {
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");

        Assert.Equal(HttpStatusCode.Forbidden, (await Mcp(pass, DemoSeed.Pepsi, "tools/list")).StatusCode);
        var call = await Result(await Mcp(pass, DemoSeed.Pepsi, "tools/call", new { name = "list_documents" }));
        Assert.True(call.GetProperty("isError").GetBoolean());
        Assert.Null(warden.Tools.Last);
        Assert.Equal("pass is for a different engagement", Audited(pass, "list_documents").Reason);
    }

    [Fact]
    public async Task notification_is_202_and_unknown_method_is_a_json_rpc_error()
    {
        var pass = await warden.Pass(DemoSeed.Sara, DemoSeed.FirmA, DemoSeed.Coke, "documents.read");
        var client = warden.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", pass);

        var note = await client.PostAsJsonAsync($"/mcp/engagements/{DemoSeed.Coke}", new { jsonrpc = "2.0", method = "notifications/initialized" });
        Assert.Equal(HttpStatusCode.Accepted, note.StatusCode);

        var bad = await (await Mcp(pass, DemoSeed.Coke, "resources/list")).Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(-32601, bad.GetProperty("error").GetProperty("code").GetInt32());
    }
}
