/*
 * HealthController.cs — GET /health, returns {"status": "ok"}.
 *
 * A controller is a class that groups HTTP endpoints (like an APIRouter in FastAPI).
 *   [ApiController]  — turns on JSON input/output and automatic 400s for bad input.
 *   [Route("health")] — URL prefix for every endpoint in this class.
 *   [HttpGet]         — this method answers GET requests.
 * ControllerBase gives helpers like Ok(...) (200), NotFound() (404), Forbid() (403).
 *
 * Used by Docker/Azure to check the app is alive.
 */
using Microsoft.AspNetCore.Mvc;

namespace Warden.Api.Controllers;

[ApiController]
[Route("health")]
public class HealthController : ControllerBase
{
    [HttpGet]
    public IActionResult Get() => Ok(new { status = "ok" });
}
