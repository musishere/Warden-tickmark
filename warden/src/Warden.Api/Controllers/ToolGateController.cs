/*
 * ToolGateController.cs — POST /tools/{tool}/invoke: the plain-HTTP door to the tool gate.
 *
 *   Authorization: Bearer <agent pass>        (from /token/exchange, NOT a sign-in token)
 *   { ...the tool's own arguments... }        (passed through untouched)
 *
 * All the checking lives in Auth/ToolGate.cs (pass -> decision -> audit log -> forward),
 * shared with the MCP door. This file only turns the gate's answer into an HTTP reply:
 *   refused                -> the gate's 401/403/502 + {"error", "reason"}
 *   the tool's MCP result  -> 200 + the tool's text (Tickmark's tools answer JSON text);
 *                             isError (no such document...) -> 422 {"error": "tool_error", "reason": text}
 * ponytail: kept for agents that don't speak MCP yet (tickmark's warden_client.py); goes in step 19.
 *
 * C# notes:
 *   [AllowAnonymous] — skip the sign-in token check (Program.cs's fallback policy): this
 *     endpoint takes an agent PASS, which the gate checks itself.
 *   async Task<IActionResult> / await — like Python's async def / await.
 */
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;

namespace Warden.Api.Controllers;

[ApiController]
[Route("tools/{tool}/invoke")]
[AllowAnonymous]
public class ToolGateController(ToolGate gate) : ControllerBase
{
    [HttpPost]
    public async Task<IActionResult> Invoke(string tool, [FromBody] JsonElement args)
    {
        var result = await gate.Run(Request.Headers.Authorization.ToString(), tool, args);
        if (!result.McpResult)
            return new ContentResult { StatusCode = result.Status, Content = result.Body, ContentType = "application/json" };

        // { "content": [ { "type": "text", "text": ... }, ... ], "isError": bool } -> the text, joined.
        var mcp = JsonDocument.Parse(result.Body).RootElement;
        var text = string.Concat(mcp.GetProperty("content").EnumerateArray()
            .Where(c => c.TryGetProperty("text", out _)).Select(c => c.GetProperty("text").GetString()));
        if (mcp.TryGetProperty("isError", out var e) && e.ValueKind == JsonValueKind.True)
            return StatusCode(422, new { error = "tool_error", reason = text });
        return new ContentResult { StatusCode = 200, Content = text, ContentType = "application/json" };
    }
}
