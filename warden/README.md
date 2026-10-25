# Warden

Identity and access for AI agents: narrow 15-minute passes, one rulebook (engagements, roles,
ethical walls), a gate in front of every tool call, an append-only audit log, and an off switch.
C# / ASP.NET Core, PostgreSQL, Microsoft Entra ID.

Everything (plain words, technical detail, diagrams, how to run it):
**[docs/how-it-works.md](docs/how-it-works.md)** · Plan and progress: [MVP.md](MVP.md)

Run locally: `docker compose up -d`, `dotnet run --project src/Warden.Api -- migrate`, then
`dotnet run --project src/Warden.Api` (http://localhost:5000; the firm admin's page is `/admin/`).
Put it in Azure: [infra/deploy.sh](infra/deploy.sh) ([how-it-works §25](docs/how-it-works.md#25-going-live-in-azure-and-the-lock)).
