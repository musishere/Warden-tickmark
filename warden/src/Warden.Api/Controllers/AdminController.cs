/*
 * AdminController.cs — /admin/...: the firm admin's endpoints, behind the admin page (step 22).
 *
 * Who gets in (EntraSignIn.AdminPolicy): a Microsoft sign-in with the "Warden.Admin" app role, made
 * at Warden's own admin page. Not an agent, even one acting for an admin; not a dev token.
 * Which firm: always the admin's own, from the verified token (tid), never from the URL or body.
 * Anything of another firm's is "not found", exactly like something that doesn't exist.
 *
 *   GET    /admin/sign-in                               public: where the page sends you to sign in (22g)
 *   GET    /admin/firm                                  which firm you administer (+ roles, scopes for the page)
 *   GET    /admin/people                                your firm's people + their engagements and roles
 *   POST   /admin/people   {name, email, entraObjectId?}  add a person (entraObjectId: their Microsoft account)
 *   PUT    /admin/people/{id}/entra   {objectId}        link a person to their Microsoft account
 *   PUT    /admin/engagements/{e}/members/{user} {role} put a person on an engagement as Staff/Manager/Admin
 *   DELETE /admin/engagements/{e}/members/{user}        take them off it
 *
 *   GET    /admin/engagements                           engagements + their members and ethical walls (22c)
 *   POST   /admin/engagements   {name}                  open one
 *   PUT    /admin/engagements/{e}/walls/{user} {reason} wall a person off it (beats every role, even Admin)
 *   DELETE /admin/engagements/{e}/walls/{user}          lift the wall
 *
 *   GET    /admin/agents                                agents your firm allows, with their ceilings (22d)
 *   PUT    /admin/agents/{entra client id} {name, allowedScopes}  allow one / change its ceiling
 *                                                        ("" = allowed nothing: off, history kept)
 *
 *   GET    /admin/servers                               your MCP servers behind Warden + assigned tools (22e)
 *   POST   /admin/servers   {name, url, audience}       connect one (https, except on a dev machine)
 *   GET    /admin/servers/{id}/tools                    ask the server what it offers, assigned or not
 *   PUT    /admin/servers/{id}/tools/{tool} {scope}     let agents use a tool, needing that scope
 *   DELETE /admin/servers/{id}/tools/{tool}             hide it again (agents get "unknown tool")
 *
 *   GET    /admin/audit?engagement=&before=&limit=      your firm's audit log, newest first, paged (22f)
 *
 * Every change is a row in the audit log (Action "admin.*", UserId = the admin who did it), in the
 * same transaction as the change: done AND logged, or neither.
 * Taking someone off, walling them, lowering an agent's ceiling or hiding a tool needs no revocation:
 * every tool call re-decides with today's data (Decision.cs), so it holds from the next call on.
 * ponytail: the Microsoft account is typed in as its Object ID (Entra → Users → the person); a
 * search box needs Microsoft Graph, and SCIM user sync (Later) makes this step go away.
 *
 * C# notes:
 *   [Authorize(Policy = ...)] — use this named rule instead of the default one (Program.cs),
 *   like an Express middleware on one router: app.use('/admin', requireAdmin, adminRouter).
 *   record — a small data class; here the shape of a JSON body, like a Pydantic model / zod schema.
 */
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using System.Text.Json;
using Warden.Api.Auth;
using Warden.Core;
using Warden.Api.Data;
using Warden.Core.Domain;

namespace Warden.Api.Controllers;

[ApiController]
[Route("admin")]
[Authorize(Policy = EntraSignIn.AdminPolicy)]
public class AdminController(WardenDbContext db, IConfiguration config, IWebHostEnvironment env,
    WardenSigningKey key, IHttpClientFactory http) : ControllerBase
{
    public record NewPerson(string Name, string Email, Guid? EntraObjectId);
    public record EntraAccount(Guid ObjectId);
    public record Membership(string Role);
    public record NewEngagement(string Name);
    public record Wall(string Reason);
    public record AgentRegistration(string Name, string AllowedScopes);
    public record NewServer(string Name, string Url, string Audience);
    public record ToolScope(string Scope);

    Guid TenantId => Guid.Parse(User.FindFirst("tid")!.Value);
    Guid AdminId => Guid.Parse(User.FindFirst("oid")!.Value);
    Guid EntraTenant => Guid.Parse(config["Entra:TenantId"]!);  // ponytail: one Entra tenant (EntraSignIn.cs)

    // What the page (wwwroot/admin) needs BEFORE sign-in: where to sign in. Nothing secret: the client
    // id and tenant are in every Microsoft sign-in address anyway. 404 when Entra is off.
    [HttpGet("sign-in")]
    [AllowAnonymous]
    public IActionResult SignIn() => EntraSignIn.IsConfigured(config)
        ? Ok(new { tenantId = config["Entra:TenantId"], clientId = config["Entra:ClientId"],
            scope = $"api://{config["Entra:ClientId"]}/{EntraSignIn.McpScope}" })
        : NotFound();

    [HttpGet("firm")]
    public IActionResult Firm()
    {
        var firm = db.Tenants.Find(TenantId)!;
        return Ok(new { firm.Id, firm.Name, admin = User.FindFirst("name")?.Value,
            roles = Enum.GetNames<Role>(), scopes = Permissions.AllScopes.Order() });  // the page's dropdowns
    }

    [HttpGet("people")]
    public IActionResult People() => Ok(db.Users
        .Where(u => u.TenantId == TenantId)
        .OrderBy(u => u.Name)
        .Select(u => new
        {
            u.Id, u.Name, u.Email, u.Active, entraObjectId = u.EntraObjectId,
            engagements = db.Members.Where(m => m.UserId == u.Id)
                .Join(db.Engagements, m => m.EngagementId, e => e.Id, (m, e) => new { e.Id, e.Name, role = m.Role.ToString() })
                .ToList(),
        })
        .ToList());

    [HttpPost("people")]
    public IActionResult AddPerson(NewPerson body)
    {
        if (string.IsNullOrWhiteSpace(body.Name) || !body.Email.Contains('@'))
            return BadRequest(new { error = "invalid_request", reason = "a name and an email are needed" });
        var email = body.Email.Trim().ToLowerInvariant();
        if (db.Users.Any(u => u.TenantId == TenantId && u.Email == email))
            return Conflict(new { error = "conflict", reason = $"{email} is already one of your people" });
        if (body.EntraObjectId is { } oid && LinkedElsewhere(oid, null))
            return Conflict(new { error = "conflict", reason = "that Microsoft account is already linked to someone" });

        var user = new User { TenantId = TenantId, Name = body.Name.Trim(), Email = email };
        if (body.EntraObjectId is { } o)
            (user.EntraTenantId, user.EntraObjectId) = (EntraTenant, o);
        db.Users.Add(user);
        Log(null, "admin.person.add", $"added {user.Name} <{email}> ({user.Id})" +
            (user.EntraObjectId is null ? "" : $", Microsoft account {user.EntraObjectId}"));
        db.SaveChanges();
        return Created($"/admin/people/{user.Id}", new { user.Id, user.Name, user.Email });
    }

    [HttpPut("people/{id:guid}/entra")]
    public IActionResult Link(Guid id, EntraAccount body)
    {
        var user = db.Users.SingleOrDefault(u => u.Id == id && u.TenantId == TenantId);
        if (user is null)
            return NotFound(new { error = "not_found", reason = "no such person in your firm" });
        if (LinkedElsewhere(body.ObjectId, id))
            return Conflict(new { error = "conflict", reason = "that Microsoft account is already linked to someone" });

        (user.EntraTenantId, user.EntraObjectId) = (EntraTenant, body.ObjectId);
        Log(null, "admin.person.link", $"{user.Name} ({user.Id}) now signs in as Microsoft account {body.ObjectId}");
        db.SaveChanges();
        return Ok(new { user.Id, entraObjectId = user.EntraObjectId });
    }

    [HttpPut("engagements/{engagementId:guid}/members/{userId:guid}")]
    public IActionResult SetMember(Guid engagementId, Guid userId, Membership body)
    {
        if (!Enum.GetNames<Role>().Contains(body.Role))  // the names only: Enum.Parse would also take "1"
            return BadRequest(new { error = "invalid_request", reason = $"role is one of: {string.Join(", ", Enum.GetNames<Role>())}" });
        var role = Enum.Parse<Role>(body.Role);
        if (Mine(engagementId, userId) is { } missing)
            return missing;

        var member = db.Members.Find(engagementId, userId);
        if (member is null)
            db.Members.Add(new Member { EngagementId = engagementId, UserId = userId, Role = role });
        else
            member.Role = role;
        Log(engagementId, "admin.member.set", $"{Name(userId)} ({userId}) is now {role}" + (member is null ? "" : " (was a member already)"));
        db.SaveChanges();
        return Ok(new { engagementId, userId, role = role.ToString() });
    }

    [HttpDelete("engagements/{engagementId:guid}/members/{userId:guid}")]
    public IActionResult RemoveMember(Guid engagementId, Guid userId)
    {
        if (Mine(engagementId, userId) is { } missing)
            return missing;
        var member = db.Members.Find(engagementId, userId);
        if (member is null)
            return NotFound(new { error = "not_found", reason = "not a member of that engagement" });

        db.Members.Remove(member);
        Log(engagementId, "admin.member.remove", $"{Name(userId)} ({userId}) taken off (was {member.Role})");
        db.SaveChanges();
        return NoContent();
    }

    // ---- 22c: engagements + ethical walls ----

    [HttpGet("engagements")]
    public IActionResult Engagements() => Ok(db.Engagements
        .Where(e => e.TenantId == TenantId)
        .OrderBy(e => e.Name)
        .Select(e => new
        {
            e.Id, e.Name,
            members = db.Members.Where(m => m.EngagementId == e.Id)
                .Join(db.Users, m => m.UserId, u => u.Id, (m, u) => new { userId = u.Id, u.Name, role = m.Role.ToString() }).ToList(),
            walls = db.EthicalWalls.Where(w => w.EngagementId == e.Id)
                .Join(db.Users, w => w.UserId, u => u.Id, (w, u) => new { userId = u.Id, u.Name, w.Reason }).ToList(),
        })
        .ToList());

    [HttpPost("engagements")]
    public IActionResult AddEngagement(NewEngagement body)
    {
        if (string.IsNullOrWhiteSpace(body.Name))
            return BadRequest(new { error = "invalid_request", reason = "a name is needed" });
        var engagement = new Engagement { TenantId = TenantId, Name = body.Name.Trim() };
        db.Engagements.Add(engagement);
        Log(engagement.Id, "admin.engagement.add", $"opened {engagement.Name}");
        db.SaveChanges();
        return Created($"/admin/engagements/{engagement.Id}", new { engagement.Id, engagement.Name });
    }

    [HttpPut("engagements/{engagementId:guid}/walls/{userId:guid}")]
    public IActionResult SetWall(Guid engagementId, Guid userId, Wall body)
    {
        if (string.IsNullOrWhiteSpace(body.Reason))
            return BadRequest(new { error = "invalid_request", reason = "say why (it's shown in every refusal and in the audit log)" });
        if (Mine(engagementId, userId) is { } missing)
            return missing;
        var wall = db.EthicalWalls.Find(engagementId, userId);
        if (wall is null)
            db.EthicalWalls.Add(new EthicalWall { EngagementId = engagementId, UserId = userId, Reason = body.Reason.Trim() });
        else
            wall.Reason = body.Reason.Trim();
        Log(engagementId, "admin.wall.set", $"{Name(userId)} ({userId}) walled off: {body.Reason.Trim()}");
        db.SaveChanges();
        return Ok(new { engagementId, userId, reason = body.Reason.Trim() });
    }

    [HttpDelete("engagements/{engagementId:guid}/walls/{userId:guid}")]
    public IActionResult RemoveWall(Guid engagementId, Guid userId)
    {
        if (Mine(engagementId, userId) is { } missing)
            return missing;
        var wall = db.EthicalWalls.Find(engagementId, userId);
        if (wall is null)
            return NotFound(new { error = "not_found", reason = "no wall for that person there" });
        db.EthicalWalls.Remove(wall);
        Log(engagementId, "admin.wall.remove", $"{Name(userId)} ({userId}) no longer walled off (was: {wall.Reason})");
        db.SaveChanges();
        return NoContent();
    }

    // ---- 22d: agents ----

    [HttpGet("agents")]
    public IActionResult Agents() => Ok(db.Agents
        .Where(a => a.TenantId == TenantId)
        .OrderBy(a => a.Name)
        .Select(a => new { a.Id, a.Name, entraClientId = a.EntraClientId, a.AllowedScopes })
        .ToList());

    [HttpPut("agents/{clientId:guid}")]
    public IActionResult RegisterAgent(Guid clientId, AgentRegistration body)
    {
        var scopes = Permissions.Parse(body.AllowedScopes);
        var unknown = scopes.Except(Permissions.AllScopes).ToList();
        if (string.IsNullOrWhiteSpace(body.Name) || unknown.Count > 0)
            return BadRequest(new { error = "invalid_request",
                reason = $"a name, and scopes from: {string.Join(' ', Permissions.AllScopes)}" + (unknown.Count > 0 ? $" (not: {string.Join(' ', unknown)})" : "") });
        var allowed = string.Join(' ', scopes.Order());
        var agent = db.Agents.SingleOrDefault(a => a.TenantId == TenantId && a.EntraClientId == clientId);
        if (agent is null)
            db.Agents.Add(agent = new Agent { TenantId = TenantId, Name = body.Name.Trim(), EntraClientId = clientId });
        var before = agent.AllowedScopes;
        (agent.Name, agent.AllowedScopes) = (body.Name.Trim(), allowed);
        Log(null, "admin.agent.set", $"{agent.Name} (Entra app {clientId}) may be given: \"{allowed}\" (was: \"{before}\")");
        db.SaveChanges();
        return Ok(new { agent.Id, agent.Name, entraClientId = clientId, allowedScopes = allowed });
    }

    // ---- 22e: the firm's MCP servers behind Warden, and which of their tools agents may use ----

    [HttpGet("servers")]
    public IActionResult Servers() => Ok(db.UpstreamServers
        .Where(s => s.TenantId == TenantId)
        .OrderBy(s => s.Name)
        .Select(s => new
        {
            s.Id, s.Name, s.Url, s.Audience,
            tools = db.UpstreamTools.Where(t => t.ServerId == s.Id).OrderBy(t => t.Name).Select(t => new { t.Name, t.Scope }).ToList(),
        })
        .ToList());

    [HttpPost("servers")]
    public IActionResult AddServer(NewServer body)
    {
        // https only, except on a dev machine (local tool servers are http://localhost).
        // ponytail: any https address is accepted; a SaaS Warden would also refuse private/internal
        // addresses (SSRF), when firms other than the operator's own get admin access.
        if (string.IsNullOrWhiteSpace(body.Name) || string.IsNullOrWhiteSpace(body.Audience)
            || !Uri.TryCreate(body.Url, UriKind.Absolute, out var url)
            || !(url.Scheme == Uri.UriSchemeHttps || (env.IsDevelopment() && url.Scheme == Uri.UriSchemeHttp)))
            return BadRequest(new { error = "invalid_request", reason = "a name, an audience, and an https:// MCP address" });
        if (db.UpstreamServers.Any(s => s.TenantId == TenantId && s.Name == body.Name.Trim()))
            return Conflict(new { error = "conflict", reason = $"you already have a server called {body.Name.Trim()}" });
        if (db.UpstreamServers.Any(s => s.Audience == body.Audience.Trim()))
            // Unique across ALL firms: a token Warden signs for one server must never be accepted by another.
            return Conflict(new { error = "conflict", reason = "that audience is taken; pick one only this server accepts" });

        var server = new UpstreamServer { TenantId = TenantId, Name = body.Name.Trim(), Url = url.ToString(), Audience = body.Audience.Trim() };
        db.UpstreamServers.Add(server);
        Log(null, "admin.server.add", $"connected {server.Name} at {server.Url} (audience {server.Audience}); its tools stay hidden until assigned");
        db.SaveChanges();
        return Created($"/admin/servers/{server.Id}", new { server.Id, server.Name, server.Url, server.Audience });
    }

    // What the server offers right now, each with its scope (null = hidden from agents).
    // Warden signs in to it like it does for an agent's call: a one-minute token for that server's audience,
    // on the admin's behalf (no agent, no engagement).
    [HttpGet("servers/{id:guid}/tools")]
    public async Task<IActionResult> ServerTools(Guid id)
    {
        var server = db.UpstreamServers.SingleOrDefault(s => s.Id == id && s.TenantId == TenantId);
        if (server is null)
            return NotFound(new { error = "not_found", reason = "no such server in your firm" });
        var asAdmin = new DelegatedToken { TenantId = TenantId, UserId = AdminId, Scope = "", IssuedAt = DateTime.UtcNow, ExpiresAt = DateTime.UtcNow };
        JsonElement result;
        try
        {
            result = await UpstreamMcp.Request(http.CreateClient("upstream"), server,
                AgentPass.CreateForward(asAdmin, "tools/list", "", server.Audience, key), "tools/list", new { });
        }
        catch (Exception e)
        {
            return StatusCode(502, new { error = "upstream_unavailable", reason = $"{server.Name} didn't answer: {e.Message}" });
        }
        var assigned = db.UpstreamTools.Where(t => t.TenantId == TenantId).ToList();
        return Ok(result.GetProperty("tools").EnumerateArray().Select(t =>
        {
            var name = t.GetProperty("name").GetString()!;
            var mine = assigned.SingleOrDefault(a => a.Name == name);
            return new
            {
                name,
                description = t.TryGetProperty("description", out var d) ? d.GetString() : null,
                scope = mine?.ServerId == id ? mine.Scope : null,
                takenBy = mine is not null && mine.ServerId != id ? "another of your servers" : null,
            };
        }).ToList());
    }

    [HttpPut("servers/{id:guid}/tools/{tool}")]
    public IActionResult AssignTool(Guid id, string tool, ToolScope body)
    {
        if (!Permissions.AllScopes.Contains(body.Scope))
            return BadRequest(new { error = "invalid_request", reason = $"scope is one of: {string.Join(' ', Permissions.AllScopes)}" });
        var server = db.UpstreamServers.SingleOrDefault(s => s.Id == id && s.TenantId == TenantId);
        if (server is null)
            return NotFound(new { error = "not_found", reason = "no such server in your firm" });
        var row = db.UpstreamTools.Find(TenantId, tool);
        if (row is not null && row.ServerId != id)
            return Conflict(new { error = "conflict", reason = $"you already have a tool called {tool} on another server" });
        if (row is null)
            db.UpstreamTools.Add(new UpstreamTool { TenantId = TenantId, Name = tool, ServerId = id, Scope = body.Scope });
        else
            row.Scope = body.Scope;
        Log(null, "admin.tool.assign", $"{server.Name}/{tool} usable with {body.Scope}");
        db.SaveChanges();
        return Ok(new { server = server.Name, tool, scope = body.Scope });
    }

    [HttpDelete("servers/{id:guid}/tools/{tool}")]
    public IActionResult HideTool(Guid id, string tool)
    {
        var row = db.UpstreamTools.Find(TenantId, tool);
        if (row is null || row.ServerId != id)
            return NotFound(new { error = "not_found", reason = "no such assigned tool on that server" });
        db.UpstreamTools.Remove(row);
        Log(null, "admin.tool.hide", $"{tool} hidden from agents (was {row.Scope})");
        db.SaveChanges();
        return NoContent();
    }

    // ---- 22f: the audit log, the whole firm's ----

    // Newest first, `limit` rows (at most 500); the next page: ?before=<the last id you got>.
    [HttpGet("audit")]
    public IActionResult Audit([FromQuery] Guid? engagement, [FromQuery] long? before, [FromQuery] int limit = 100)
    {
        var rows = db.AuditLog.Where(a => a.TenantId == TenantId);
        if (engagement is not null)
            rows = rows.Where(a => a.EngagementId == engagement);
        if (before is not null)
            rows = rows.Where(a => a.Id < before);
        return Ok(rows.OrderByDescending(a => a.Id).Take(Math.Clamp(limit, 1, 500))
            .Select(a => new { a.Id, a.At, a.UserId, a.AgentId, a.EngagementId, a.Action, a.Scope, a.Allowed, a.Reason, a.Jti })
            .ToList());
    }

    // Both in the admin's own firm? Otherwise 404, the same as not existing (nothing leaks).
    IActionResult? Mine(Guid engagementId, Guid userId)
    {
        if (!db.Engagements.Any(e => e.Id == engagementId && e.TenantId == TenantId))
            return NotFound(new { error = "not_found", reason = "no such engagement in your firm" });
        if (!db.Users.Any(u => u.Id == userId && u.TenantId == TenantId))
            return NotFound(new { error = "not_found", reason = "no such person in your firm" });
        return null;
    }

    // A Microsoft account signs in as ONE Warden person (the unique index would refuse it anyway).
    bool LinkedElsewhere(Guid objectId, Guid? self) =>
        db.Users.Any(u => u.EntraTenantId == EntraTenant && u.EntraObjectId == objectId && u.Id != self);

    string Name(Guid userId) => db.Users.Find(userId)!.Name;

    void Log(Guid? engagementId, string action, string reason) => db.AuditLog.Add(new AuditEntry
    {
        At = DateTime.UtcNow, TenantId = TenantId, UserId = AdminId, EngagementId = engagementId,
        Action = action, Allowed = true, Reason = reason,
    });
}
