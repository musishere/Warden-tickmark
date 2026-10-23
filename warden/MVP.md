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
- [x] 6. `POST /token/exchange` (stored by `jti`) — `agent_cannot_exceed_user_permissions`, `expired_delegated_token_is_rejected`

### Phase 3 — The rules
- [x] 7. Decision function: walls → membership → role → tool policy → scope, with a reason — `ethical_wall_blocks_even_admin`, `tenant_a_cannot_read_tenant_b`
- [x] 8. Append-only audit log + `GET /audit?engagement=`

### Phase 4 — The gate
- [x] 9. `POST /tools/{tool}/invoke`: check pass (signature, expiry, audience, not revoked) → decide → log → forward; any error = deny
- [x] 10. Tools: `list_documents`, `read_document`, `run_vouch`; `export_documents` denied to agents — `prompt_injection_cannot_trigger_export`

### Phase 5 — Tickmark through Warden
- [x] 11. Tickmark's tool endpoints accept only Warden-forwarded calls
- [x] 12. Tickmark vouches via `WardenClient`: Coke allowed, Pepsi denied, both logged

### Phase 6 — The off switch
- [x] 13. Revoke by `jti` + `PATCH /scim/v2/Users/{id}` revokes all agent passes; running work stops — `scim_deactivation_revokes_agent_tokens`

### Phase 7 — Real Entra ID
- [ ] 14. Entra test tenant app registration; accept Entra tokens (`tid`, `oid`) — code + fake-Entra tests done; the real app registration is a manual step: [docs/how-it-works.md §22](docs/how-it-works.md#22-real-microsoft-entra-id-setup)

### Phase 8 — Write it up
- [x] 15. RFC, threat model, README (problem → architecture → trade-offs)

### Phase 9 — Any agent, no code changes (Warden as an MCP gateway)
Goal: a client's agent, built with anything, is pointed at Warden by changing one setting (its MCP
tool server address). No Warden code inside the agent. And it can't go around Warden.
Warden is for any agent and any tool server: it sits in front of the firm's own MCP servers and
learns their tools from them. Tickmark is only the demo agent and demo tool server.

- [x] 16. `/mcp/engagements/{id}`: Warden speaks MCP (JSON-RPC over Streamable HTTP): `initialize`,
  `tools/list` (only the tools this user + agent may use; export never listed), `tools/call` →
  the existing gate (pass check → decision → audit log → forward). One URL per engagement, so
  "which client" is part of the address — `mcp_call_is_checked_and_logged_like_any_tool_call`
- [ ] 17. MCP sign-in, the standard way: `/.well-known/oauth-protected-resource` (RFC 9728) pointing
  at Entra; 401 + `WWW-Authenticate` when no token; Warden turns the Entra token into a pass
  itself, per session — `mcp_client_without_sign_in_gets_401_with_where_to_sign_in`
- [ ] 17a. Any agent, proven: the agent is who Entra says it is, not who it claims. Each agent is
  an Entra app registration; Warden reads the client id from the signed token (`azp`) and maps it
  to a row in `agents` (like `link-entra` does for users). Unknown client id → refused until the
  firm's admin registers it: `dotnet run -- register-agent "<name>" <entra client id> "<allowed scopes>"`.
  Token exchange stops trusting the `agent_id` in the request body —
  `unknown_agent_is_refused_even_with_a_valid_sign_in`
- [x] 18. Per-agent ceiling: each registered agent gets `allowed_scopes`; a pass is
  agent ceiling ∩ user's rights ∩ what was asked — `agent_cannot_exceed_its_registration`
- [x] 18a. Warden in front of any MCP server: the firm's admin connects upstream MCP servers
  (address + how Warden signs in to each; agents never see either). `tools/list` asks each upstream
  for its tools; a per-tool table maps each one to a scope. A tool with no scope assigned is hidden
  and refused (fail closed: a tool an upstream adds tomorrow stays off until an admin says so).
  `tools/call` → the gate → the upstream that owns the tool —
  `upstream_tool_is_hidden_until_an_admin_assigns_a_scope`
- [ ] 18b. Tickmark's tools become one MCP server behind Warden, like any other. Remove the
  hand-written `Permissions.Tools` menu, `Tools:BaseUrl` and the plain-HTTP forward path, so every
  tool takes the same route — `tickmark_tools_work_as_an_upstream_mcp_server`
- [ ] 19. Proof with a stock client: MCP Inspector (or Claude Desktop) pointed at Warden, no Warden
  code: Coke works, Pepsi refused, export not offered, all in the audit log. Tickmark drops
  `warden_client.py` and uses a plain MCP client. A second, non-Tickmark MCP server behind Warden
  shows it isn't Tickmark-specific
- [ ] 20. The lock: agents hold no keys and can only reach Warden (Azure Container Apps network
  rules); Warden holds the keys to the real systems — `agent_container_cannot_reach_tools_directly`
- [ ] 21. Write-up in docs/how-it-works.md: "integrating any agent without code changes", how Warden compares with
  existing MCP gateways and Entra Agent ID

## Later
Workspaces, not engagements: rename engagement → workspace in code, data and URLs
(`/mcp/workspaces/{id}`); each firm picks the word its people see ("Engagement", "Matter",
"Project"); membership comes from the firm's identity system (an Entra group per workspace,
synced with SCIM Groups) instead of seed data. Token exchange already uses the standard
`resource` field for it.

Credits (reserve → settle, concurrency test), human approval (`override_requires_human_approval`),
Excel add-in sign-in, short-lived storage links (Azure Blob SAS) for large files, a proxy mode
for agents that don't speak MCP, dashboard.
