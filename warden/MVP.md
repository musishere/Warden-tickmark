# Warden: MVP plan

Goal: prove one claim end to end. An agent acts for a user with a narrow, short-lived pass, every
tool call is authorized centrally, and every decision is logged.

Each step is small: a few files, one test that proves it, and a commit.

## Design calls
- C# / .NET 10, ASP.NET Core controllers, EF Core, PostgreSQL (host port 5433).
- Sign-in: a local dev issuer first (offline, tests); Microsoft Entra ID in phase 7, same code path.
- Agent passes: Warden-signed JWTs, 15 min, `sub` = user, `act` = agent, engagement, scope, `jti`.
  Request shape follows OAuth token exchange (RFC 8693).
- A real proxy: Tickmark's tools sit behind Warden and accept only calls Warden forwards.
- Audit log: append-only, enforced by a database trigger.
- Fail closed: if Warden cannot decide, the answer is no.

## Steps

### Phase 0 — Ready to build
- [x] 1. `dotnet` on PATH, Postgres via Docker Compose, CI runs `dotnet test`

### Phase 1 — The data
- [x] 2. Tenants, users, agents, engagements, members (roles); first migration
- [x] 3. Ethical walls + demo seed (Firm A: Coke, Pepsi with a wall; Firm B: Acme; Sara, Ali, admin; Tickmark agent)

### Phase 2 — Identity
- [x] 4. Validate the user's sign-in token; tenant from the verified token only
- [x] 5. Warden's signing key + JWKS endpoint
- [ ] 6. `POST /token/exchange` (stored by `jti`) — `agent_cannot_exceed_user_permissions`, `expired_delegated_token_is_rejected`

### Phase 3 — The rules
- [ ] 7. Decision function: walls → membership → role → tool policy → scope, with a reason — `ethical_wall_blocks_even_admin`, `tenant_a_cannot_read_tenant_b`
- [ ] 8. Append-only audit log + `GET /audit?engagement=`

### Phase 4 — The gate
- [ ] 9. `POST /tools/{tool}/invoke`: check pass (signature, expiry, audience, not revoked) → decide → log → forward; any error = deny
- [ ] 10. Tools: `list_documents`, `read_document`, `run_vouch`; `export_documents` denied to agents — `prompt_injection_cannot_trigger_export`

### Phase 5 — Tickmark through Warden
- [ ] 11. Tickmark's tool endpoints accept only Warden-forwarded calls
- [ ] 12. Tickmark vouches via `WardenClient`: Coke allowed, Pepsi denied, both logged

### Phase 6 — The off switch
- [ ] 13. Revoke by `jti` + `PATCH /scim/v2/Users/{id}` revokes all agent passes; running work stops — `scim_deactivation_revokes_agent_tokens`

### Phase 7 — Real Entra ID
- [ ] 14. Entra test tenant app registration; accept Entra tokens (`tid`, `oid`)

### Phase 8 — Write it up
- [ ] 15. RFC, threat model, README (problem → architecture → trade-offs)

## Later
Credits (reserve → settle, concurrency test), human approval (`override_requires_human_approval`),
Excel add-in sign-in, MCP-compatible proxy, Azure Container Apps deploy, dashboard.
