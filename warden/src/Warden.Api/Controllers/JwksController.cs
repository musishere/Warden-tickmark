/*
 * JwksController.cs — GET /.well-known/jwks.json: Warden's PUBLIC key(s).
 *
 * "JWKS" = JSON Web Key Set, the standard way to publish public keys (Entra does the same).
 * Tickmark downloads this once, caches it, and uses it to check the signature on every
 * agent pass Warden issues. The response looks like:
 *   { "keys": [ { "kty": "RSA", "kid": "...", "use": "sig", "alg": "RS256", "n": "...", "e": "AQAB" } ] }
 *   n, e = the public key numbers. There is NO "d", "p", "q" (those would be private).
 *
 * [AllowAnonymous]: public keys are meant to be public, and checkers have no token.
 * The ".well-known" path is a web convention for "standard info about this server".
 */
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Auth;

namespace Warden.Api.Controllers;

[ApiController]
[AllowAnonymous]
[Route(".well-known/jwks.json")]
public class JwksController(WardenSigningKey signingKey) : ControllerBase
{
    // WardenSigningKey arrives through the constructor: dependency injection (Program.cs registers it).
    [HttpGet]
    public IActionResult Get() => Ok(new
    {
        keys = new[]
        {
            new { kty = signingKey.PublicJwk.Kty, kid = signingKey.Kid, use = signingKey.PublicJwk.Use,
                  alg = signingKey.PublicJwk.Alg, n = signingKey.PublicJwk.N, e = signingKey.PublicJwk.E },
        },
    });
}
