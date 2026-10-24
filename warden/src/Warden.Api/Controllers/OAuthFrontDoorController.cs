/*
 * OAuthFrontDoorController.cs — Warden's sign-in front door for MCP clients (step 19).
 *
 * Why it exists: an MCP client sends `resource=<the MCP address>` when it signs in (RFC 8707, the
 * MCP rule), and Entra refuses any `resource` that isn't the scope's app ("AADSTS9010010: The
 * resource parameter provided in the request doesn't match with the requested scopes"). So the
 * client signs in through Warden, which passes everything on to Entra WITHOUT `resource`:
 *
 *   GET  /.well-known/oauth-authorization-server   where the doors are (RFC 8414)
 *   GET  /oauth/authorize?...   -> 302 to Entra's real sign-in page, same query minus `resource`
 *   POST /oauth/token           -> the same form posted to Entra's token endpoint minus `resource`;
 *                                  Entra's answer (the tokens, or its error) passed back as-is
 *
 * Warden doesn't sign anyone in itself: Microsoft shows the page, checks the password and MFA, and
 * issues the tokens; Warden still only accepts Microsoft-signed tokens (EntraSignIn.cs). No client
 * secret: agents are public clients with PKCE, which Entra checks.
 * ponytail: tokens pass through Warden's memory on the way back (never stored or logged).
 */
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.WebUtilities;
using Warden.Api.Auth;

namespace Warden.Api.Controllers;

[ApiController]
[AllowAnonymous]
public class OAuthFrontDoorController(IConfiguration config, IHttpClientFactory http) : ControllerBase
{
    public static string Issuer(HttpRequest request) => $"{request.Scheme}://{request.Host}";

    string Entra(string endpoint) => $"https://login.microsoftonline.com/{config["Entra:TenantId"]}/oauth2/v2.0/{endpoint}";
    string Scope => $"api://{config["Entra:ClientId"]}/{EntraSignIn.McpScope}";

    [HttpGet(".well-known/oauth-authorization-server")]
    public IActionResult Metadata()
    {
        if (!EntraSignIn.IsConfigured(config))
            return NotFound();
        var issuer = Issuer(Request);
        return Ok(new
        {
            issuer,
            authorization_endpoint = $"{issuer}/oauth/authorize",
            token_endpoint = $"{issuer}/oauth/token",
            response_types_supported = new[] { "code" },
            grant_types_supported = new[] { "authorization_code", "refresh_token" },
            code_challenge_methods_supported = new[] { "S256" },
            token_endpoint_auth_methods_supported = new[] { "none" },
            scopes_supported = new[] { Scope, "offline_access" },
        });
    }

    [HttpGet("oauth/authorize")]
    public IActionResult Authorize()
    {
        if (!EntraSignIn.IsConfigured(config))
            return NotFound();
        var query = Request.Query.Where(q => q.Key != "resource").ToDictionary(q => q.Key, q => (string?)q.Value.ToString());
        if (string.IsNullOrWhiteSpace(query.GetValueOrDefault("scope")))
            query["scope"] = Scope;  // a client that asked for nothing gets a token for Warden, not for Microsoft Graph
        return Redirect(QueryHelpers.AddQueryString(Entra("authorize"), query));
    }

    [HttpPost("oauth/token")]
    public async Task<IActionResult> Token()
    {
        if (!EntraSignIn.IsConfigured(config))
            return NotFound();
        var form = (await Request.ReadFormAsync()).Where(f => f.Key != "resource")
            .Select(f => KeyValuePair.Create(f.Key, f.Value.ToString()));
        using var res = await http.CreateClient("entra").PostAsync(Entra("token"), new FormUrlEncodedContent(form));
        return new ContentResult
        {
            StatusCode = (int)res.StatusCode,
            Content = await res.Content.ReadAsStringAsync(),
            ContentType = res.Content.Headers.ContentType?.ToString() ?? "application/json",
        };
    }
}
