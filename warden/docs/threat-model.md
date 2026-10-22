# Threat model (OWASP / OWASP GenAI)

Scope: Warden (gateway), Tickmark's tool server, and the agent between them. Design: [rfc.md](rfc.md).
Each mitigation names the code or test that implements it.

## Assets and trust boundaries

| Asset | Why it matters |
|---|---|
| Client documents (invoices, samples) | confidential; competitors' data sits in the same firm |
| Warden's signing key | anyone holding it can mint passes |
| The audit log | the firm's proof of what agents did |
| Ethical walls | a breach is a regulatory incident, not just a bug |

Boundaries: **user ↔ agent** (the agent reads untrusted documents, so treat it as compromisable),
**agent ↔ Warden** (every call authenticated by a pass), **Warden ↔ tools** (forwarded tokens only),
**firm ↔ firm** (tenants share one database).

## Threats

| # | Threat (OWASP ref) | Mitigation | Where |
|---|---|---|---|
| 1 | **Prompt injection** in an invoice makes the agent exfiltrate data (LLM01, LLM06 excessive agency) | Permissions live in Warden, not the prompt. `export_documents` is humans-only, refused even with an export scope; the tool server has no agent export endpoint | `Permissions.Tools`, `prompt_injection_cannot_trigger_export` |
| 2 | **Agent exceeds its user** (privilege escalation, LLM06) | Pass scopes ⊆ the user's role scopes; re-checked against live data on every call | `Decision`, `agent_cannot_exceed_user_permissions` |
| 3 | **Ethical wall breach** (A01 broken access control) | Walls are checked before membership and role, beat Admin, apply to agents and to audit reads | `ethical_wall_blocks_even_admin`, `AuditTests` |
| 4 | **Cross-tenant access** (A01) | Firm only from verified tokens; other firms' engagements answer "not found"; audit reads filtered by firm | `tenant_a_cannot_read_tenant_b` |
| 5 | **Stolen pass** (A07) | 15 min, one engagement, audience-bound to the gate, revocable by `jti` | `AgentPass.Check`, `RevokeTests` |
| 6 | **Agent bypasses Warden**, calls tools directly | Tools accept only Warden-signed forwarded tokens (aud `tickmark-tools`, 60 s, one tool); the agent's pass is refused | `tickmark/tests/test_warden.py` |
| 7 | **Token confusion** (sign-in token used as a pass, pass used at the tools) | Distinct issuers / audiences, each pinned by its checker | `PassCheckTests`, `ToolGateTests` |
| 8 | **Forged passes** (A02) | RSA signature; private key never leaves `WardenSigningKey`; JWKS publishes only `n`, `e` | `SigningKeyTests` |
| 9 | **Leaver keeps access** | SCIM `active=false` deactivates the user and revokes every live pass in one transaction | `scim_deactivation_revokes_agent_tokens` |
| 10 | **Tampered audit trail** (A09) | Postgres triggers refuse UPDATE / DELETE / TRUNCATE; pass and audit row commit together | `audit_log_is_append_only` |
| 11 | **Path traversal** in document names (A01) | Tools use only the file name inside the token's engagement folder | `test_warden.py` |
| 12 | **Warden outage turns into open access** | Client and tools fail closed: `WardenDenied`, 401 without JWKS | `test_warden.py` |
| 13 | **SCIM endpoint abuse** | Per-firm secret, constant-time compare; a secret only reaches its own firm's users; only `active` can change | `RevokeTests` |
| 14 | **Forged Entra token / wrong app / other tenant** (A07) | Entra keys from metadata, issuer and audience pinned, account must be linked to a Warden user | `EntraTests` |
| 15 | **Cost abuse** (LLM10 unbounded consumption) | *Not built.* Designed: credit reservation per call at the gate | RFC §7 |
| 16 | **Agent impersonation** (one agent claims another's id) | *Partial.* Pass bound to the user's verified token and rights; agent credentials are future work | RFC §9 |

## Residual risks (accepted for the MVP)

- Dev sign-in tokens use a shared HMAC key, trusted **only** in Development (no key in Production).
- In Development the signing key is regenerated at startup (passes die on restart); Production
  requires `Signing:PrivateKeyPem` (Key Vault).
- An in-flight `run_vouch` finishes after revocation; the next call stops.
- Unreadable passes are logged to the app log, not the audit log.
- Audit reads return the whole trail (no paging, no export controls).
