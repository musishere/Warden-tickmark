/*
 * MeController.cs — GET /me: "who does Warden think you are?"
 *
 * Everything here is read from the VERIFIED token (User.FindFirst reads its claims).
 * By the time this code runs, ASP.NET Core has already checked the token's signature,
 * expiry, issuer and audience, and the fallback policy in Program.cs has checked that
 * oid + tid are present. A bad token never reaches this method: the caller gets 401.
 *
 * The tenant comes ONLY from the token, never from the URL or body, so a caller can't
 * claim to be in another firm.
 *
 * No [Authorize] attribute needed: endpoints are locked by default (Program.cs).
 */
using Microsoft.AspNetCore.Mvc;

namespace Warden.Api.Controllers;

[ApiController]
[Route("me")]
public class MeController : ControllerBase
{
    [HttpGet]
    public IActionResult Get() => Ok(new
    {
        userId = User.FindFirst("oid")!.Value,   // "!" = "trust me, not null" (the policy guarantees it)
        tenantId = User.FindFirst("tid")!.Value,
        name = User.FindFirst("name")?.Value,
    });
}
