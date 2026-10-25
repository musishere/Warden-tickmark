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
 * Two claims are kept as they are (step 17): azp = the app (agent) Entra issued the token to
 * (`appid` in older v1 tokens), and uti = the token's own id.
 *
 * MCP sign-in (step 17): agents ask Microsoft for a token with the scope api://<client id>/access_as_user
 * (McpScope), which Warden's app registration exposes. AuthorizationServer is what Warden tells
 * them in /.well-known/oauth-protected-resource.
 * An Entra account with no linked Warden user -> 401.
 *
 * Admin page sign-in (step 22): `roles` is kept too: the app roles the firm's Entra admin assigned
 * this person on Warden's app registration. AdminPolicy lets in only `Warden.Admin` AND a token
 * issued to Warden's own app (azp = Entra:ClientId, i.e. the admin page). Entra puts the user's
 * roles into their agents' tokens as well, so without the azp check an admin's agent would be an
 * admin too; with it, no agent is ever an admin, whoever it acts for.
 *
 * ponytail: one Entra tenant. Multi-tenant (many firms, each with its own Entra) needs an issuer
 * check per known tenant instead of one fixed Authority.
 */
using System.Security.Claims;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Warden.Api.Data;

namespace Warden.Api.Auth;

public static class EntraSignIn
{
    public const string Scheme = "Entra";
    public const string McpScope = "access_as_user";  // the scope Warden's app registration exposes (how-it-works §22)
    public const string AdminRole = "Warden.Admin";   // app role value on Warden's app registration
    public const string AdminPolicy = "WardenAdmin";  // [Authorize(Policy = EntraSignIn.AdminPolicy)]

    public static string AuthorizationServer(IConfiguration config) => $"https://login.microsoftonline.com/{config["Entra:TenantId"]}/v2.0";

    public static bool IsConfigured(IConfiguration config) =>
        !string.IsNullOrEmpty(config["Entra:TenantId"]) && !string.IsNullOrEmpty(config["Entra:ClientId"]);

    public static void Configure(JwtBearerOptions o, IConfiguration config)
    {
        var tenant = config["Entra:TenantId"];
        var client = config["Entra:ClientId"]!;
        o.Authority = AuthorizationServer(config);
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
        List<Claim> claims = [new("oid", user.Id.ToString()), new("tid", user.TenantId.ToString()), new("name", user.Name)];
        if ((ctx.Principal!.FindFirst("azp") ?? ctx.Principal.FindFirst("appid"))?.Value is { } app)
            claims.Add(new("azp", app));
        if (ctx.Principal.FindFirst("uti")?.Value is { } uti)
            claims.Add(new("uti", uti));
        claims.AddRange(ctx.Principal.FindAll("roles").Select(r => new Claim("roles", r.Value)));
        ctx.Principal = new ClaimsPrincipal(new ClaimsIdentity(claims, Scheme));
        return Task.CompletedTask;
    }

    // Who may use /admin: see the header. Entra off -> nobody (fail closed); dev tokens never.
    public static void Admin(AuthorizationPolicyBuilder p, IConfiguration config)
    {
        if (!IsConfigured(config))
        {
            p.RequireAssertion(_ => false);
            return;
        }
        p.AddAuthenticationSchemes(Scheme)  // only Entra sign-ins count here, not dev tokens
            .RequireClaim("oid").RequireClaim("tid")
            .RequireClaim("roles", AdminRole)
            .RequireClaim("azp", config["Entra:ClientId"]!);
    }
}
