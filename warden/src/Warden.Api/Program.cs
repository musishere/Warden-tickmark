/*
 * Program.cs — the entry point of the Warden web API (like main.py in a FastAPI app).
 *
 * .NET runs this file top to bottom when the app starts:
 *   1. CreateBuilder: reads appsettings.json and sets up logging.
 *   2. builder.Services: registers things the app needs ("dependency injection",
 *      like FastAPI's Depends). AddControllers = "find every Controller class",
 *      AddDbContext = "give out database sessions".
 *   3. Build + MapControllers: wires each controller's routes (e.g. GET /health).
 *   4. Run: starts the HTTP server and blocks until it stops.
 *
 * The last line ("public partial class Program;") lets the test project start
 * this app in memory (WebApplicationFactory<Program>), like FastAPI's TestClient.
 */
using Microsoft.EntityFrameworkCore;
using Warden.Api.Data;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();

// The database: Postgres, connection string "Warden" from appsettings.json.
// Any controller that asks for a WardenDbContext in its constructor gets one.
builder.Services.AddDbContext<WardenDbContext>(o => o
    .UseNpgsql(builder.Configuration.GetConnectionString("Warden"))
    .UseSnakeCaseNamingConvention());

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

app.MapControllers();
app.Run();

public partial class Program;
