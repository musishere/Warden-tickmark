# Threat model (OWASP / OWASP GenAI)

| Threat | Mitigation |
|---|---|
| Prompt injection in documents | Permissions enforced in Warden, not the prompt; no export tool |
| Agent privilege escalation | Delegated scope <= user permissions; checked on every call |
| Stolen agent token | Short expiry, audience-bound, revocable by jti |
| Cross-tenant leak | Tenant from verified token only; tenant-scoped queries |
| Leaver keeps access | SCIM deactivation revokes all delegated tokens |
| Cost abuse | Credit reservation and per-agent limits |
| Tampered audit trail | Append-only log; no update/delete path |
