# Contributing to Warden

Thanks for helping. Warden decides what AI agents may do with a firm's data, so the bar is simple:
**every promise is a test, and every change keeps all of them.**

## Set up

Requirements: [.NET 10 SDK](https://dotnet.microsoft.com/download), Docker, and Node.js 22+ (only
if you change the admin page).

```bash
cd warden
docker compose up -d                                 # PostgreSQL on localhost:5433
dotnet test                                          # all tests (creates the warden_test database)
dotnet run --project src/Warden.Api -- migrate
dotnet run --project src/Warden.Api -- seed          # demo firms, people, walls, an agent
dotnet run --project src/Warden.Api                  # http://localhost:5000
```

Real Microsoft sign-in and the admin page need an Entra app registration (about 10 minutes):
[docs/how-it-works.md §22](warden/docs/how-it-works.md#22-real-microsoft-entra-id-setup).

### The admin page

Source in `warden/admin-ui` (React, TypeScript, shadcn/ui, Tailwind). The built page is committed in
`src/Warden.Api/wwwroot/admin`, so nothing else needs Node.

```bash
cd warden/admin-ui
npm ci
npm run watch            # rebuilds into wwwroot/admin on save while Warden runs
npm run build            # before committing: CI fails if the committed build doesn't match the source
```

## How we work

- **Fail closed.** If Warden can't decide (an error, a missing row, an unreachable server), the answer
  is no. New code paths follow suit.
- **Tests are promises.** Name a test after the guarantee it proves, in plain English:
  `ethical_wall_blocks_even_admin`, not `TestWall2`. A security check isn't done until you've broken
  it on purpose once and watched its test fail.
- **One decision function.** Authorization rules live in `Auth/Decision.cs`. Don't add a second place
  that says yes.
- **The firm comes from the verified token,** never from a URL or request body. Another firm's
  things are "not found", the same as things that don't exist.
- **Every change is audited.** Anything that changes who may do what writes an audit row in the same
  transaction as the change.
- **Explain the why.** Every C# file starts with a `/* */` header saying what it's for; comment the
  reasons, not the obvious. Keep `docs/how-it-works.md` in step with behaviour changes.
- **Migrations are append-only.** Never edit an old one; add a new one
  (`dotnet ef migrations add <Name> --project src/Warden.Api`).
- **Small and boring.** Prefer the standard library and the framework over new dependencies; ask in
  the issue before adding one.

## Pull requests

1. Open an issue first for anything larger than a fix, so we can agree on the approach.
2. Keep each pull request to one change, with its tests and doc updates.
3. `dotnet test` passes locally; if you touched `admin-ui`, `npm run build` output is committed.
4. Describe what changes for an agent, an admin or an auditor, and how you verified it.

Security issues: please don't open a pull request or issue. See [SECURITY.md](SECURITY.md).

By contributing, you agree your contribution is licensed under the project's
[Apache License 2.0](LICENSE).
