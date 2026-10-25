# Security policy

Warden is an access-control gateway, so security reports are its most important bug reports. Thank you
for taking the time.

## Reporting a vulnerability

**Please don't open a public issue.** Report privately through GitHub instead:
**Security** tab → **Report a vulnerability** (GitHub private vulnerability reporting).

Please include:

- what an attacker can do, and what they need first (e.g. "a signed-in agent of firm A can read firm B's audit log")
- steps to reproduce: requests, tokens with secrets redacted, configuration
- the commit or version you tested

What to expect:

| | |
|---|---|
| Acknowledgement | within 3 working days |
| First assessment | within 10 working days |
| Fix and disclosure | coordinated with you; credit given unless you'd rather not |

## Scope

In scope: everything under `warden/`: the API and gateway, token handling, the decision function,
the admin API and page, SCIM, the audit log, and the Azure templates in `warden/infra/`.

Especially interesting: anything that lets an agent or person get past the checks Warden promises:
another firm's data, an ethical wall, a role or agent ceiling, a tool that wasn't assigned, the admin
API from an agent's token, changing or deleting audit rows, or reaching a tool server without Warden.

Out of scope: the `tickmark/` demo application; Development-mode-only behaviour (dev tokens, the
ephemeral signing key) unless it can be triggered in Production; denial of service by volume.

## Supported versions

Warden is pre-1.0. Security fixes go to the `main` branch; there are no maintained release branches yet.

## Security design

The threat model and each defence's test: [warden/docs/how-it-works.md §16](warden/docs/how-it-works.md#16-threats-and-defences).
