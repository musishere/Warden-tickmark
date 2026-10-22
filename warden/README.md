# Warden

**Identity and access for AI agents.** An agent never gets a user's full powers: it trades the
user's sign-in for a narrow, 15-minute pass, and every tool call goes through Warden, which
decides, logs, and only then forwards. C# / ASP.NET Core, PostgreSQL, Microsoft Entra ID.

The sample agent is [Tickmark](../tickmark) (Python), which vouches invoices for audit firms.

## The problem

Audit firms want AI agents to do real work on client documents. Hand the agent the auditor's
session and one prompt injection in one invoice can reach everything the auditor can, on every
engagement, including a competitor's behind an ethical wall. Afterwards nobody can prove what
the agent touched or for whom. The model being "told not to" is not a control.

## How Warden works

```
Sara ──sign-in (Entra)──▶ Tickmark ──POST /token/exchange──▶ Warden: 15-min pass, Coke only, read + vouch
                              │
                              └──POST /tools/run_vouch/invoke + pass──▶ Warden ──60-s token──▶ Tickmark's tools
                                                                     check · decide · log · forward
```

- **Narrow passes** (RFC 8693 shape): `sub` = user, `act` = agent, one engagement, named scopes,
  15 minutes, a `jti` in a logbook so they can be revoked.
- **One decision function**: ethical walls → membership → role → tool policy → scope, re-run on
  live data for every call, always with a reason.
- **Agents can't export.** Not because the prompt says so: the gate refuses `export_documents`
  for any agent, Admin or not.
- **Append-only audit log**, enforced by Postgres triggers. Every decision, allowed or denied.
- **Off switch**: revoke one pass, or SCIM-deactivate a leaver and every pass dies at the next call.
- **Fail closed** everywhere: no pass, no log, no Warden, no JWKS → no.

What's really proven, by name (`tests/Warden.Tests/SecurityGuaranteeTests.cs`):
`agent_cannot_exceed_user_permissions` · `ethical_wall_blocks_even_admin` ·
`tenant_a_cannot_read_tenant_b` · `expired_delegated_token_is_rejected` ·
`prompt_injection_cannot_trigger_export` · `scim_deactivation_revokes_agent_tokens`

## Trade-offs

A central gateway costs one hop and one indexed lookup per tool call; in return there is one
place for the rules, one log, and one off switch, and the agent can't route around it (tools
accept only Warden-signed forwarded tokens). Revocation takes effect at the next call, not
mid-call. Credits, human approval and agent credentials are designed but not built. Full
reasoning: [docs/rfc.md](docs/rfc.md). Threats and mitigations: [docs/threat-model.md](docs/threat-model.md).

## Run it

Needs the .NET 10 SDK, Docker, and (for the agent) Python 3.12 + uv.

```bash
cd warden
docker compose up -d                                     # Postgres on :5433
dotnet ef database update -p src/Warden.Api              # tables
dotnet run --project src/Warden.Api -- seed              # demo world: Firm A (Coke, Pepsi + wall), Firm B
dotnet test                                              # 50 tests
dotnet run --project src/Warden.Api --urls http://localhost:5000
```

The whole flow, with Tickmark (second terminal):

```bash
cd tickmark && make setup && make warden-demo            # demo invoices for Coke and Pepsi
make run                                                 # Tickmark's tools on :8000 (behind Warden)
SARA=$(cd ../warden && dotnet run --project src/Warden.Api -- dev-token sara)   # third terminal
make agent TOKEN=$SARA                                   # Coke: ✅ ✅ ✅ ✅ ⚠️ ❓
make agent TOKEN=$SARA ENG=e0000000-0000-0000-0000-000000000002   # Pepsi: "ethical wall", stops
```

Real Entra ID sign-in instead of dev tokens: [docs/entra.md](docs/entra.md).

## Map

| | |
|---|---|
| [MVP.md](MVP.md) | the plan, step by step, and what's done |
| [docs/how-it-works.md](docs/how-it-works.md) | a walkthrough of every part, with diagrams |
| [docs/rfc.md](docs/rfc.md) | design doc: problem, options, decision, failure modes |
| [docs/threat-model.md](docs/threat-model.md) | OWASP / OWASP GenAI threats → mitigations → tests |
| `src/Warden.Core` | plain C#: domain classes, roles → scopes, tool policy |
| `src/Warden.Api/Auth` | passes, signing key, the decision function, Entra sign-in |
| `src/Warden.Api/Controllers` | exchange, tool gate, revoke, SCIM, audit, JWKS |
