# Warden

Identity and access layer for AI agents: delegated tokens, engagement-based
permissions with ethical walls, and an audit trail. C# / ASP.NET Core,
PostgreSQL, Microsoft Entra ID.

Scope and status: [MVP.md](MVP.md). Design: [docs/rfc.md](docs/rfc.md).
Threats: [docs/threat-model.md](docs/threat-model.md).

## Run
```bash
docker compose up -d
dotnet run --project src/Warden.Api
dotnet test
```
Needs the .NET 10 SDK. Sample agent: [../tickmark](../tickmark).
