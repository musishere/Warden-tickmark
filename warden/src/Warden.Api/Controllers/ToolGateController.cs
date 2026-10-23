/*
 * ToolGateController.cs — POST /tools/{tool}/invoke: the plain-HTTP door to the tool gate.
 *
 *   Authorization: Bearer <agent pass>        (from /token/exchange, NOT a sign-in token)
 *   { ...the tool's own arguments... }        (passed through untouched)
 *
 * All the checking lives in Auth/ToolGate.cs (pass -> decision -> audit log -> forward),
 * shared with the MCP door. This file only turns the gate's answer into an HTTP reply.
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
        return new ContentResult { StatusCode = result.Status, Content = result.Body, ContentType = result.ContentType };
    }
}
