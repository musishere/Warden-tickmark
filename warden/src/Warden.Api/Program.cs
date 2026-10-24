/*
 * Program.cs — the entry point of the Warden web API (like main.py in a FastAPI app).
 *
 * .NET runs this file top to bottom when the app starts:
 *   1. CreateBuilder: reads appsettings.json and sets up logging.
 *   2. builder.Services: registers things the app needs ("dependency injection",
 *      like FastAPI's Depends). AddControllers = "find every Controller class",
 *      AddDbContext = "give out database sessions",
 *      AddAuthentication = "read and verify the sign-in token on each request",
 *      AddAuthorization  = "decide if that caller may use the endpoint",
 *      AddSingleton<WardenSigningKey> = "one shared key pair for signing agent passes",
 *      AddHttpClient("upstream") = "how to reach the firm's MCP servers behind the gate".
 *   3. Build, then the middleware pipeline: each request passes through
 *      UseAuthentication -> UseAuthorization -> the controller, in that order.
 *   4. Run: starts the HTTP server and blocks until it stops.
 *
 * Command-line extras (run, do the job, exit):
 *   dotnet run -- seed             insert the demo data (Data/DemoSeed.cs)
 *   dotnet run -- dev-token sara [tickmark]   print a dev sign-in token for a demo user (issued to that agent)
 *   dotnet run -- link-entra sara <tid> <oid>   let a real Entra account sign in as that user
 *   dotnet run -- add-upstream "Firm A" <name> <mcp url> <audience>   connect a firm's MCP server
 *   dotnet run -- assign-tool "Firm A" <server> <tool> <scope>        let agents use one of its tools
 *   dotnet run -- register-agent <name> <entra client id> "<scopes>"   let an agent (an Entra app) in
 *
 * The last line ("public partial class Program;") lets the test project start
 * this app in memory (WebApplicationFactory<Program>), like FastAPI's TestClient.
 */
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using Warden.Api.Auth;
using Warden.Api.Data;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();

// The database: Postgres, connection string "Warden" from appsettings.json.
// Any controller that asks for a WardenDbContext in its constructor gets one.
builder.Services.AddDbContext<WardenDbContext>(o => o
    .UseNpgsql(builder.Configuration.GetConnectionString("Warden"))
    .UseSnakeCaseNamingConvention());

// Sign-in tokens: "Authorization: Bearer <JWT>" on every request.
var auth = builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme).AddJwtBearer(o =>
{
    o.MapInboundClaims = false;  // keep claim names as sent ("oid", "tid"); .NET renames them otherwise
    o.TokenValidationParameters = new TokenValidationParameters
    {
        ValidIssuer = DevIssuer.Issuer,
        ValidAudience = DevIssuer.Audience,
        ClockSkew = TimeSpan.Zero,  // expired means expired (default allows 5 minutes of slack)
    };
    // Only trust the dev issuer's key on a dev machine / in tests. In Production no key
    // is set, so every token fails the signature check -> 401 (fail closed) until Entra (step 14).
    if (builder.Environment.IsDevelopment())
        o.TokenValidationParameters.IssuerSigningKey = DevIssuer.SigningKey(builder.Configuration);
});

// Real Microsoft Entra ID sign-in, when Entra:TenantId + Entra:ClientId are set (Auth/EntraSignIn.cs).
// A request may then sign in with either kind of token ("schemes"); both end up as a Warden user.
string[] signInSchemes = [JwtBearerDefaults.AuthenticationScheme];
if (EntraSignIn.IsConfigured(builder.Configuration))
{
    auth.AddJwtBearer(EntraSignIn.Scheme, o => EntraSignIn.Configure(o, builder.Configuration));
    signInSchemes = [.. signInSchemes, EntraSignIn.Scheme];
}

// Warden's own key pair for signing agent passes (Auth/WardenSigningKey.cs).
// Singleton = one instance for the whole app, so every pass is signed with the same key.
builder.Services.AddSingleton<WardenSigningKey>();

// The firm's MCP servers behind the gate (Auth/UpstreamMcp.cs): every approved call goes to one of
// them. Tests swap in a fake (WardenFactory.cs). 5 minutes: a tool like Tickmark's run_vouch reads
// every PDF, it can take a while.
builder.Services.AddHttpClient("upstream", c => c.Timeout = TimeSpan.FromMinutes(5));

// Entra's token endpoint, for MCP clients signing in through Warden's front door
// (Controllers/OAuthFrontDoorController.cs). Tests swap in a fake.
builder.Services.AddHttpClient("entra");

// The tool gate (Auth/ToolGate.cs). Scoped = one per request, sharing that request's database session.
builder.Services.AddScoped<ToolGate>();

// Locked by default: every endpoint requires a verified token that says who (oid) and
// which firm (tid). An endpoint is public only if it says [AllowAnonymous] (like /health).
builder.Services.AddAuthorizationBuilder().SetFallbackPolicy(new AuthorizationPolicyBuilder(signInSchemes)
    .RequireAuthenticatedUser()
    .RequireClaim("oid")
    .RequireClaim("tid")
    .Build());

var app = builder.Build();

// `dotnet run -- seed`: insert the demo data (Data/DemoSeed.cs), then exit instead of serving.
// CreateScope gives a short-lived set of services, like opening and closing a DB session.
if (args.Contains("seed"))
{
    using var scope = app.Services.CreateScope();
    DemoSeed.Run(scope.ServiceProvider.GetRequiredService<WardenDbContext>());
    Console.WriteLine("Demo data ready (skipped anything already there).");
    return;
}

// `dotnet run -- link-entra sara <tid> <oid>`: let a real Entra account sign in as a demo user (step 14).
if (args.Length == 4 && args[0] == "link-entra")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
    var user = db.Users.Single(u => u.Name.ToLower() == args[1].ToLower());
    (user.EntraTenantId, user.EntraObjectId) = (Guid.Parse(args[2]), Guid.Parse(args[3]));
    db.SaveChanges();
    Console.WriteLine($"{user.Name} now signs in with Entra account {args[3]} (tenant {args[2]}).");
    return;
}

// `dotnet run -- add-upstream "Firm A" docs https://docs-mcp.firma.example/mcp firm-a-docs`:
// connect one of a firm's own MCP servers behind Warden (name, MCP address, token audience).
// Its tools stay hidden until assign-tool says which scope each one needs (step 18a).
if (args.Length == 5 && args[0] == "add-upstream")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
    var tenant = db.Tenants.Single(t => t.Name.ToLower() == args[1].ToLower());
    db.UpstreamServers.Add(new Warden.Core.Domain.UpstreamServer { TenantId = tenant.Id, Name = args[2], Url = args[3], Audience = args[4] });
    db.SaveChanges();
    Console.WriteLine($"{tenant.Name}: {args[2]} connected at {args[3]}. Its tools are hidden until you assign-tool them.");
    return;
}

// `dotnet run -- assign-tool "Firm A" docs search documents.read`: let agents use one tool of an
// upstream server, needing that scope. Run again to change the scope.
if (args.Length == 5 && args[0] == "assign-tool")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
    var tenant = db.Tenants.Single(t => t.Name.ToLower() == args[1].ToLower());
    var server = db.UpstreamServers.Single(u => u.TenantId == tenant.Id && u.Name == args[2]);
    if (!Warden.Core.Permissions.AllScopes.Contains(args[4]))
    {
        Console.Error.WriteLine($"unknown scope {args[4]}; one of: {string.Join(' ', Warden.Core.Permissions.AllScopes)}");
        Environment.ExitCode = 1;
        return;
    }
    var row = db.UpstreamTools.Find(tenant.Id, args[3]);
    if (row is null)
        db.UpstreamTools.Add(new Warden.Core.Domain.UpstreamTool { TenantId = tenant.Id, Name = args[3], ServerId = server.Id, Scope = args[4] });
    else if (row.ServerId != server.Id)
    {
        Console.Error.WriteLine($"{tenant.Name} already has a tool called {args[3]} on another server");
        Environment.ExitCode = 1;
        return;
    }
    else
        row.Scope = args[4];
    db.SaveChanges();
    Console.WriteLine($"{tenant.Name}: agents may now use {args[2]}/{args[3]} with {args[4]}.");
    return;
}

// `dotnet run -- register-agent Tickmark <entra client id> "documents.read vouch.run"`: let an agent
// in (step 17a). The client id is the agent's Entra app registration; Warden knows the agent by it
// (the `azp` of its sign-in tokens). The scopes are its ceiling (step 18). Run again to change them.
if (args.Length == 4 && args[0] == "register-agent")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
    var unknown = Warden.Core.Permissions.Parse(args[3]).Except(Warden.Core.Permissions.AllScopes).ToList();
    if (unknown.Count > 0)
    {
        Console.Error.WriteLine($"unknown scope {string.Join(' ', unknown)}; known: {string.Join(' ', Warden.Core.Permissions.AllScopes)}");
        Environment.ExitCode = 1;
        return;
    }
    var client = Guid.Parse(args[2]);
    var agent = db.Agents.SingleOrDefault(a => a.Name.ToLower() == args[1].ToLower());
    if (agent is null)
        db.Agents.Add(agent = new Warden.Core.Domain.Agent { Name = args[1] });
    (agent.EntraClientId, agent.AllowedScopes) = (client, args[3]);
    db.SaveChanges();
    Console.WriteLine($"{agent.Name} (Entra app {client}) may now be given: {args[3]}");
    return;
}

// `dotnet run -- dev-token sara tickmark`: print a 1-hour sign-in token for a seeded user, issued to
// that registered agent (its client id goes in `azp`, like Entra does), then exit.
if (args.Length is 2 or 3 && args[0] == "dev-token")
{
    using var scope = app.Services.CreateScope();
    var db = scope.ServiceProvider.GetRequiredService<WardenDbContext>();
    var user = db.Users.Single(u => u.Name.ToLower() == args[1].ToLower());
    var agent = args.Length == 3 ? db.Agents.Single(a => a.Name.ToLower() == args[2].ToLower()).EntraClientId : null;
    Console.WriteLine(DevIssuer.CreateToken(DevIssuer.SigningKey(app.Configuration),
        user.Id, user.TenantId, user.Name, DateTime.UtcNow.AddHours(1), agent));
    return;
}

app.UseAuthentication();  // read + verify the token, fill in User (the caller's claims)
app.UseAuthorization();   // apply the policy: no valid token -> 401
app.MapControllers();
app.Run();

public partial class Program;
