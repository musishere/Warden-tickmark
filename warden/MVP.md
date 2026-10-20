# Warden: MVP scope

Goal: prove one claim end to end. An agent acts for a user with a narrow,
short-lived token, every tool call is authorized centrally, and every decision is logged.

## In scope
- [ ] Entra ID JWT validation (`tid`, `oid`); tenant taken from the verified token only
- [ ] `POST /token/exchange`: delegated token with `sub` + `act`, scope, resource, audience, 15 min expiry, `jti`
- [ ] Engagements, members and roles; ethical walls as deny rules that override everything
- [ ] `POST /tools/{tool}/invoke`: authorize, then log, then forward. Fail closed
- [ ] Append-only `audit_log` and `GET /audit?engagement=`
- [ ] Token revocation by `jti`
- [ ] PostgreSQL via EF Core; Docker Compose for local dev
- [ ] Security tests (see `tests/Warden.Tests/SecurityGuaranteeTests.cs`):
  - `agent_cannot_exceed_user_permissions`
  - `ethical_wall_blocks_even_admin`
  - `expired_delegated_token_is_rejected`
  - `tenant_a_cannot_read_tenant_b`
  - `prompt_injection_cannot_trigger_export`

## Deferred (after MVP)
- Credits: reserve → settle ledger and the concurrency test
- Human approval flow (`override_requires_human_approval`)
- SCIM deactivation → revoke all agent tokens
- Policy engine beyond simple per-tool allow/deny
- MCP-compatible proxy
- Azure Container Apps deploy, Service Bus
- Dashboard UI

## Done when
Tickmark can vouch invoices through Warden, a wall-blocked request is denied and
logged, and the five MVP security tests pass in CI.
