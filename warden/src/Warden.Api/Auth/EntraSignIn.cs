/*
 * EntraSignIn.cs — accept real Microsoft Entra ID sign-in tokens (step 14), alongside dev tokens.
 *
 * Switched on only when BOTH are set (else Warden behaves exactly as before):
 *   Entra:TenantId   your Entra tenant's id ("Directory (tenant) ID" in the portal)
 *   Entra:ClientId   Warden's app registration ("Application (client) ID")
 *
 * How a token is checked (ASP.NET Core's JwtBearer does this part):
 *   - signature: Entra's public keys, fetched from
 *     https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
 *   - issuer:   that tenant only (v2 "login.microsoftonline.com/<tenant>/v2.0", or v1 "sts.windows.net/<tenant>/")
 *   - audience: Warden's client id, or "api://<client id>"
 *   - not expired
 * Then MapToWardenUser: the token's tid + oid must be LINKED to a Warden user
 * (users.entra_tenant_id / entra_object_id; `dotnet run -- link-entra sara <tid> <oid>`).
 * The request continues as that Warden user: oid/tid become Warden's own ids, so every
 * controller, the decision function and the audit log work exactly as with dev tokens.
 * An Entra account with no linked Warden user -> 401.
 *
 * ponytail: one Entra tenant. Multi-tenant (many firms, each with its own Entra) needs an issuer
 * check per known tenant instead of one fixed Authority.
 */
using System.Security.Claims;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Warden.Api.Data;

namespace Warden.Api.Auth;

public static class EntraSignIn
{
    public const string Scheme = "Entra";

    public static bool IsConfigured(IConfiguration config) =>
        !string.IsNullOrEmpty(config["Entra:TenantId"]) && !string.IsNullOrEmpty(config["Entra:ClientId"]);

    public static void Configure(JwtBearerOptions o, IConfiguration config)
    {
        var tenant = config["Entra:TenantId"];
        var client = config["Entra:ClientId"]!;
        o.Authority = $"https://login.microsoftonline.com/{tenant}/v2.0";
        o.MapInboundClaims = false;  // keep "oid", "tid" as Entra sends them
        o.TokenValidationParameters.ValidAudiences = [client, $"api://{client}"];
        o.TokenValidationParameters.ValidIssuers =
            [$"https://login.microsoftonline.com/{tenant}/v2.0", $"https://sts.windows.net/{tenant}/"];
        o.TokenValidationParameters.ClockSkew = TimeSpan.Zero;
        o.Events = new JwtBearerEvents { OnTokenValidated = MapToWardenUser };
    }

    // Entra says who it is (tid + oid); Warden says which of ITS users that is.
    static Task MapToWardenUser(TokenValidatedContext ctx)
    {
        Guid.TryParse(ctx.Principal?.FindFirst("tid")?.Value, out var tid);
        Guid.TryParse(ctx.Principal?.FindFirst("oid")?.Value, out var oid);
        var db = ctx.HttpContext.RequestServices.GetRequiredService<WardenDbContext>();
        var user = db.Users.SingleOrDefault(u => u.EntraTenantId == tid && u.EntraObjectId == oid);
        if (user is null)
        {
            ctx.Fail("this Entra account is not linked to a Warden user");
            return Task.CompletedTask;
        }
        ctx.Principal = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim("oid", user.Id.ToString()), new Claim("tid", user.TenantId.ToString()), new Claim("name", user.Name)],
            Scheme));
        return Task.CompletedTask;
    }
}
