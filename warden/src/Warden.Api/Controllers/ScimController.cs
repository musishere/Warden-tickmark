/*
 * ScimController.cs — PATCH /scim/v2/Users/{id}: the firm's identity provider says "this person left".
 *
 * SCIM (RFC 7644) is how identity providers like Microsoft Entra ID keep apps' user lists in step.
 * When someone leaves, Entra sends:
 *   PATCH /scim/v2/Users/<id>
 *   Authorization: Bearer <the firm's SCIM secret>
 *   { "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
 *     "Operations": [{ "op": "Replace", "path": "active", "value": "False" }] }
 * (Entra really sends "False" as a STRING; other providers send false, or { "active": false } with
 * no path. All three are understood.)
 *
 * Deactivating (active = false):
 *   1. users.active = false: from now on Decision refuses everything for them ("user deactivated"),
 *      so no new passes
 *   2. every live pass of theirs is revoked: the agent's next tool call is refused, so running
 *      work stops at that call
 *   3. one audit row per revoked pass ("scim.deactivate"), in each engagement's trail
 * active = true turns them back on (old passes stay revoked).
 *
 * Who may call: the firm's identity provider, with that firm's secret from config
 *   Scim:Tenants:<tenant id> = <secret>      (env var: Scim__Tenants__<tenant id>=<secret>)
 * The secret decides the firm, and only that firm's users can be changed. No secret configured,
 * wrong secret -> 401. Another firm's user or unknown id -> 404 (same answer, nothing leaks).
 * ponytail: secrets in config; a SCIM client table with hashed secrets when firms onboard themselves.
 */
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Warden.Api.Data;
using Warden.Core.Domain;

namespace Warden.Api.Controllers;

[ApiController]
[Route("scim/v2/Users")]
[AllowAnonymous]  // not a user's sign-in: the identity provider's SCIM secret, checked below
public class ScimController(WardenDbContext db, IConfiguration config, ILogger<ScimController> log) : ControllerBase
{
    [HttpPatch("{id:guid}")]
    public IActionResult Patch(Guid id, [FromBody] JsonElement body)
    {
        var tenantId = FirmFromSecret();
        if (tenantId is null)
            return ScimError(401, "invalid SCIM secret");

        var user = db.Users.Find(id);
        if (user is null || user.TenantId != tenantId)
            return ScimError(404, "user not found");

        var active = ActiveFrom(body);
        if (active is null)
            return ScimError(400, "only the 'active' attribute can be changed");

        user.Active = active.Value;
        var revoked = new List<DelegatedToken>();
        if (!active.Value)
        {
            var now = DateTime.UtcNow;
            revoked = db.DelegatedTokens.Where(t => t.UserId == id && t.RevokedAt == null).ToList();
            foreach (var pass in revoked)
            {
                pass.RevokedAt = now;
                db.AuditLog.Add(new AuditEntry
                {
                    At = now, TenantId = user.TenantId, UserId = id, AgentId = pass.AgentId, EngagementId = pass.EngagementId,
                    Action = "scim.deactivate", Scope = pass.Scope, Allowed = true,
                    Reason = "user deactivated by the identity provider: pass revoked", Jti = pass.Jti,
                });
            }
        }
        db.SaveChanges();  // one transaction: deactivated AND revoked, or neither
        log.LogInformation("SCIM: user {User} active={Active}, {Count} passes revoked", id, active, revoked.Count);

        return Ok(new
        {
            schemas = new[] { "urn:ietf:params:scim:schemas:core:2.0:User" },
            id = user.Id, userName = user.Email, displayName = user.Name, active = user.Active,
        });
    }

    // Which firm's secret is this? Compared in constant time, so timing can't reveal it.
    Guid? FirmFromSecret()
    {
        var header = Request.Headers.Authorization.ToString();
        if (!header.StartsWith("Bearer "))
            return null;
        var given = Encoding.UTF8.GetBytes(header["Bearer ".Length..]);
        foreach (var firm in config.GetSection("Scim:Tenants").GetChildren())
            if (!string.IsNullOrEmpty(firm.Value) && Guid.TryParse(firm.Key, out var tid)
                && CryptographicOperations.FixedTimeEquals(given, Encoding.UTF8.GetBytes(firm.Value)))
                return tid;
        return null;
    }

    // The new value of "active" from a SCIM PatchOp, or null if the request doesn't set it.
    static bool? ActiveFrom(JsonElement body)
    {
        if (Prop(body, "Operations") is not { ValueKind: JsonValueKind.Array } ops)
            return null;
        bool? result = null;
        foreach (var op in ops.EnumerateArray())
        {
            if (!string.Equals(Prop(op, "op")?.GetString(), "replace", StringComparison.OrdinalIgnoreCase))
                return null;
            var path = Prop(op, "path")?.GetString();
            var value = Prop(op, "value");
            if (string.Equals(path, "active", StringComparison.OrdinalIgnoreCase) && value is { } v)
                result = AsBool(v);
            else if (path is null && value is { ValueKind: JsonValueKind.Object } obj && Prop(obj, "active") is { } inner)
                result = AsBool(inner);
            else
                return null;  // anything else: we don't sync other attributes yet
            if (result is null)
                return null;
        }
        return result;
    }

    // SCIM attribute names are case-insensitive (RFC 7643): "Operations" = "operations".
    static JsonElement? Prop(JsonElement obj, string name)
    {
        if (obj.ValueKind != JsonValueKind.Object)
            return null;
        foreach (var p in obj.EnumerateObject())
            if (string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase))
                return p.Value;
        return null;
    }

    // true, false, "True", "False" (Entra) -> bool; anything else -> null.
    static bool? AsBool(JsonElement v) => v.ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.String when bool.TryParse(v.GetString(), out var b) => b,
        _ => null,
    };

    ObjectResult ScimError(int status, string detail) => StatusCode(status, new
    {
        schemas = new[] { "urn:ietf:params:scim:api:messages:2.0:Error" },
        status = status.ToString(), detail,
    });
}
