/*
 * UpstreamMcp.cs — Warden as an MCP CLIENT, talking to a firm's own MCP server (step 18a).
 *
 * The other side of McpController: there, agents talk MCP to Warden; here, Warden talks MCP to
 * the real systems behind it. Request(...) sends one request in a short session of its own:
 *
 *   initialize                 -> the server may answer with an Mcp-Session-Id header;
 *                                 every later message carries it back
 *   notifications/initialized  -> "ready" (no answer expected)
 *   the request itself         -> tools/list or tools/call; returns its "result"
 *
 * Servers may answer a POST with plain JSON or with an event stream (text/event-stream, lines of
 * "data: {...}"); both are read. A JSON-RPC error, or anything unreadable, throws
 * HttpRequestException: the same "the tool server did not answer" as a server that's down.
 *
 * Every message carries Warden's one-minute token for this server (aud = UpstreamServer.Audience).
 *
 * ponytail: a fresh session per request (3 round trips). Keep one session per server if
 * latency matters. tools/list reads the first page only (no nextCursor paging).
 */
using System.Net.Http.Headers;
using System.Text.Json;
using Warden.Core.Domain;

namespace Warden.Api.Auth;

public static class UpstreamMcp
{
    const string Version = "2025-06-18";

    public static async Task<JsonElement> Request(HttpClient http, UpstreamServer server, string token, string method, object @params)
    {
        var (session, _) = await Send(http, server, token, null, new
        {
            jsonrpc = "2.0", id = 1, method = "initialize",
            @params = new { protocolVersion = Version, capabilities = new { }, clientInfo = new { name = "warden", version = "0.1" } },
        });
        await Send(http, server, token, session, new { jsonrpc = "2.0", method = "notifications/initialized" });

        var (_, reply) = await Send(http, server, token, session, new { jsonrpc = "2.0", id = 2, method, @params });
        if (reply is not { } r || !r.TryGetProperty("result", out var result))
            throw new HttpRequestException($"{server.Name} answered {method} with an error: {reply?.GetRawText()}");
        return result;
    }

    // POST one message; returns the session id the server set (if any) and its JSON-RPC reply (null for none).
    static async Task<(string? Session, JsonElement? Reply)> Send(HttpClient http, UpstreamServer server, string token, string? session, object message)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, server.Url) { Content = JsonContent.Create(message) };
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        req.Headers.Accept.ParseAdd("application/json");
        req.Headers.Accept.ParseAdd("text/event-stream");
        req.Headers.Add("MCP-Protocol-Version", Version);
        if (session is not null)
            req.Headers.Add("Mcp-Session-Id", session);

        using var res = await http.SendAsync(req);
        res.EnsureSuccessStatusCode();  // 4xx/5xx -> HttpRequestException
        var newSession = res.Headers.TryGetValues("Mcp-Session-Id", out var s) ? s.First() : session;
        var body = await res.Content.ReadAsStringAsync();
        if (string.IsNullOrWhiteSpace(body))
            return (newSession, null);

        try
        {
            // An event stream: the reply is the "data:" line that carries a JSON-RPC id.
            if (res.Content.Headers.ContentType?.MediaType == "text/event-stream")
                return (newSession, body.Split('\n')
                    .Where(l => l.StartsWith("data:"))
                    .Select(l => JsonDocument.Parse(l["data:".Length..]).RootElement)
                    .First(m => m.TryGetProperty("id", out _)));
            return (newSession, JsonDocument.Parse(body).RootElement);
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException)  // not JSON / no reply in the stream
        {
            throw new HttpRequestException($"{server.Name} sent an unreadable answer", e);
        }
    }
}
