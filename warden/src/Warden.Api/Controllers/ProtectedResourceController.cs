/*
 * ProtectedResourceController.cs — GET /.well-known/oauth-protected-resource/... (RFC 9728, step 17).
 *
 * How a stock MCP client signs in to Warden, with no Warden code inside it:
 *   1. it calls /mcp/engagements/{id} with no token -> 401, WWW-Authenticate: Bearer
 *      resource_metadata="https://warden/.well-known/oauth-protected-resource/mcp/engagements/{id}"
 *   2. it reads that (this file):
 *        { "resource": "https://warden/mcp/engagements/{id}",
 *          "authorization_servers": ["https://login.microsoftonline.com/<tenant>/v2.0"],
 *          "scopes_supported": ["api://<Warden's client id>/access_as_user"] }
 *   3. it opens Microsoft's sign-in page (Entra is the authorization server, not Warden), the user
 *      signs in, and the client gets an Entra token for Warden (aud = Warden's app, azp = the agent's app)
 *   4. it calls /mcp again with that token; Warden makes the pass itself (McpController, Delegation.cs)
 *
 * The path after oauth-protected-resource is the address it describes (RFC 9728 section 3.1);
 * with nothing after it, Warden as a whole. 404 when Entra isn't set up: there's nowhere to sign in.
 * ponytail: the address is built from the request's Host; behind a proxy, set forwarded headers (step 20).
 */
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;

namespace Warden.Api.Controllers;

[ApiController]
[Route(".well-known/oauth-protected-resource/{**path}")]
[AllowAnonymous]
public class ProtectedResourceController(IConfiguration config) : ControllerBase
{
    [HttpGet]
    public IActionResult Get(string? path)
    {
        if (!EntraSignIn.IsConfigured(config))
            return NotFound();
        return Ok(new
        {
            resource = $"{Request.Scheme}://{Request.Host}/{path}".TrimEnd('/'),
            authorization_servers = new[] { EntraSignIn.AuthorizationServer(config) },
            scopes_supported = new[] { $"api://{config["Entra:ClientId"]}/{EntraSignIn.McpScope}" },
            bearer_methods_supported = new[] { "header" },
            resource_name = "Warden",
        });
    }

    public static string MetadataUrl(HttpRequest request, string path) =>
        $"{request.Scheme}://{request.Host}/.well-known/oauth-protected-resource/{path}";
}
