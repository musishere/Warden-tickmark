# How Warden works (so far)

A walkthrough of everything built up to **step 7** of [MVP.md](../MVP.md), with diagrams.
Diagrams are [Mermaid](https://mermaid.js.org): GitHub and VS Code (with a Mermaid extension)
draw them as pictures.

**Warden in one sentence:** an AI agent never gets a user's full powers. It trades the user's
sign-in token for a narrow, 15-minute **pass**, and every tool call is checked against that pass.

---

## 1. The big picture

```mermaid
flowchart LR
    Sara([Sara<br/>auditor at Firm A])
    Login[Sign-in service<br/>now: DevIssuer<br/>later: Entra ID]
    Agent[Tickmark agent]
    subgraph W[Warden API]
        Auth[Step 4<br/>check sign-in token]
        Ex[Step 6b<br/>POST /token/exchange]
        Jwks[Step 5<br/>GET /.well-known/jwks.json]
        Gate[Step 9 - not built<br/>tool gate]
    end
    DB[(Postgres<br/>tenants, users, agents,<br/>engagements, members,<br/>ethical_walls,<br/>delegated_tokens)]
    Tools[Tickmark tools<br/>documents, vouching]

    Sara -->|1. signs in| Login
    Login -->|2. sign-in token| Sara
    Sara -->|3. asks agent to work| Agent
    Agent -->|4. sign-in token + what it needs| Ex
    Ex --> Auth
    Ex <--> DB
    Ex -->|5. 15-min pass| Agent
    Agent -.->|6. tool call + pass| Gate
    Gate -.->|7. forward if allowed| Tools
    Tools -.->|check pass signature| Jwks
```

Solid lines are built. Dotted lines are coming (steps 8–12).

---

## 2. The projects and files

```mermaid
flowchart TB
    subgraph Core[Warden.Core - plain C#, no web, no database]
        Domain[Domain/<br/>Tenant, User, Agent, Engagement,<br/>Member + Role, EthicalWall,<br/>DelegatedToken]
        Perm[Permissions.cs<br/>role -> allowed scopes,<br/>tool -> scope + agents allowed?]
    end
    subgraph Api[Warden.Api - the web app]
        Prog[Program.cs<br/>startup, auth, DI,<br/>seed / dev-token commands]
        subgraph Data[Data/]
            Ctx[WardenDbContext.cs<br/>classes to tables]
            Mig[Migrations/<br/>generated SQL]
            Seed[DemoSeed.cs<br/>demo world]
        end
        subgraph AuthF[Auth/]
            Dev[DevIssuer.cs<br/>fake sign-in tokens]
            Key[WardenSigningKey.cs<br/>RSA key pair]
            Pass[AgentPass.cs<br/>what is in a pass]
            Dec[Decision.cs<br/>may they? + reason]
        end
        subgraph Ctrl[Controllers/]
            H[Health: GET /health]
            Me[Me: GET /me]
            J[Jwks: GET /.well-known/jwks.json]
            T[TokenExchange: POST /token/exchange]
        end
    end
    subgraph Tests[Warden.Tests - xUnit]
        F[WardenFactory.cs<br/>API in memory on warden_test]
        TT[HealthTests, AuthTests, SigningKeyTests,<br/>DatabaseTests, TokenExchangeTests,<br/>DecisionTests, SecurityGuaranteeTests]
    end
    Api --> Core
    Tests --> Api
    Ctx --> Domain
    T --> Dec
    Dec --> Perm
```

| Python world | .NET world here |
|---|---|
| `pyproject.toml` | `*.csproj` |
| FastAPI app in `main.py` | `Program.cs` |
| `APIRouter` | a `Controller` class |
| SQLAlchemy models | `Domain/*.cs` classes |
| SQLAlchemy session / Base | `WardenDbContext` |
| Alembic | `dotnet ef migrations add` / `database update` |
| `Depends(...)` | dependency injection (`builder.Services.Add...`) |
| pytest | xUnit (`[Fact]`) |
| `.env` | `appsettings.Development.json` / environment variables / User Secrets |

---

## 3. The data (Phase 1, steps 2–3)

```mermaid
erDiagram
    TENANTS ||--o{ USERS : employs
    TENANTS ||--o{ ENGAGEMENTS : runs
    ENGAGEMENTS ||--o{ MEMBERS : has
    USERS ||--o{ MEMBERS : "works on"
    ENGAGEMENTS ||--o{ ETHICAL_WALLS : "blocks"
    USERS ||--o{ ETHICAL_WALLS : "blocked by"
    USERS ||--o{ DELEGATED_TOKENS : "on behalf of"
    AGENTS ||--o{ DELEGATED_TOKENS : carries
    ENGAGEMENTS ||--o{ DELEGATED_TOKENS : "only for"
    TENANTS ||--o{ DELEGATED_TOKENS : "belongs to"

    TENANTS {
        uuid id PK
        text name
    }
    USERS {
        uuid id PK
        uuid tenant_id FK
        text email "unique per tenant"
        text name
    }
    AGENTS {
        uuid id PK
        text name
    }
    ENGAGEMENTS {
        uuid id PK
        uuid tenant_id FK
        text name
    }
    MEMBERS {
        uuid engagement_id PK
        uuid user_id PK
        text role "Staff, Manager, Admin"
    }
    ETHICAL_WALLS {
        uuid engagement_id PK
        uuid user_id PK
        text reason
    }
    DELEGATED_TOKENS {
        uuid jti PK
        uuid tenant_id FK
        uuid user_id FK
        uuid agent_id FK
        uuid engagement_id FK
        text scope
        timestamptz issued_at
        timestamptz expires_at
        timestamptz revoked_at "null until revoked"
    }
```

**What each table means**

| Table | Plain meaning |
|---|---|
| `tenants` | An audit firm. Nothing crosses between firms. |
| `users` | A person at a firm. |
| `agents` | A registered AI agent (Tickmark). Not tied to one firm. |
| `engagements` | One client audit, e.g. "Coke 2026". The unit of access. |
| `members` | "This user works on this engagement, with this role." No row = no access. |
| `ethical_walls` | "This user must NEVER touch this engagement." Beats membership and every role. |
| `delegated_tokens` | The **pass logbook**: one row per pass issued, so passes can be cancelled and audited. |

**Rules the database enforces**
- Every foreign key is `ON DELETE RESTRICT`: deleting a firm that still has users fails, nothing disappears silently.
- `members` and `ethical_walls` are keyed on `(engagement_id, user_id)`: one row per pair.
- `users` has a unique `(tenant_id, email)`.
- Names are snake_case (`tenant_id`), via `EFCore.NamingConventions`.

**How a class becomes a table**

```mermaid
flowchart LR
    A[Domain/User.cs<br/>C# class] --> B[WardenDbContext.cs<br/>keys, FKs, indexes]
    B -->|dotnet ef migrations add Name| C[Migrations/date_Name.cs<br/>generated]
    C -->|dotnet ef database update| D[(Postgres table)]
```

Migrations so far: `Initial` → `EthicalWalls` → `DelegatedTokens`. To change the schema,
edit a class or `WardenDbContext`, then **add a new migration**; never edit an old one.

**The demo world** (`dotnet run --project src/Warden.Api -- seed`, fixed ids in `DemoSeed.cs`)

```mermaid
flowchart TB
    subgraph FA[Firm A]
        subgraph Coke[Coke 2026]
            S[Sara - Staff]
            A[Ali - Manager]
            O1[Omar - Admin]
        end
        subgraph Pepsi[Pepsi 2026]
            B[Bilal - Staff]
            O2[Omar - Admin]
        end
    end
    subgraph FB[Firm B]
        subgraph Acme[Acme 2026]
            D[Dana - Staff]
        end
    end
    S -.->|walled from| Pepsi
    A -.->|walled from| Pepsi
    O1 -.->|walled from| Pepsi
    B -.->|walled from| Coke
```

Omar is an **Admin member of Pepsi and walled from it**: the case that proves walls beat roles.
Dana is in another firm: the case that proves firms can't see each other.

---

## 4. Who are you? Sign-in tokens (step 4)

A token (JWT) is `header.payload.signature`. The payload is readable by anyone; the
**signature** proves who stamped it and that nothing was changed.

```
{ "oid": "<Sara's id>", "tid": "<Firm A's id>", "name": "Sara",
  "iss": "warden-dev", "aud": "warden", "exp": <in 1 hour> }
```

**Who stamps it:** now `DevIssuer` (`dotnet run -- dev-token sara`, HMAC with a dev key,
trusted **only in Development**). Later Microsoft Entra ID via OIDC; same claim names, so only
the trusted key changes.

**What happens on every request**

```mermaid
flowchart TD
    R[Request<br/>Authorization: Bearer token] --> A1{Authentication<br/>signature OK?<br/>issuer = warden-dev?<br/>audience = warden?<br/>not expired?}
    A1 -->|no / no token| U401[401 Unauthorized<br/>who are you?]
    A1 -->|yes| A2{Authorization<br/>fallback policy:<br/>has oid and tid?}
    A2 -->|no| F403[403 Forbidden<br/>I know you, but no]
    A2 -->|yes| C[Controller runs<br/>reads oid / tid from the token]
    P[/health has AllowAnonymous/] -.->|skips both| C
```

- **Locked by default:** every endpoint needs a valid token unless marked `[AllowAnonymous]`
  (only `/health` and the JWKS). A forgotten endpoint is still protected.
- **The firm comes only from the verified token**, never from the URL or body.
- **Production** has no dev key → every dev token gets 401 (fail closed).

---

## 5. Warden's own stamp (step 5)

Passes for agents are signed with an **RSA key pair**, not a shared secret:

```mermaid
flowchart LR
    subgraph Warden
        Priv[Private key<br/>never leaves WardenSigningKey]
    end
    Priv -->|signs| PassT[Agent pass]
    Pub[Public key<br/>GET /.well-known/jwks.json<br/>n, e, kid - no d, p, q] -->|checks| PassT
    Anyone[Tickmark / anyone] -->|downloads| Pub
```

| | Can check a pass | Can create a pass |
|---|---|---|
| Holder of the public key (anyone) | yes | **no** |
| Warden (private key) | yes | yes |

So even if Tickmark were hacked, the attacker couldn't forge passes.

Where the private key comes from: config `Signing:PrivateKeyPem` (production, later from Key
Vault) → else in Development a fresh key at startup → else refuse to sign (fail closed).

---

## 6. Getting a pass: token exchange (step 6)

```mermaid
sequenceDiagram
    actor Sara
    participant T as Tickmark (agent)
    participant W as Warden /token/exchange
    participant DB as Postgres

    Sara->>T: "Vouch the Coke invoices"
    T->>W: POST /token/exchange<br/>Bearer: Sara's sign-in token<br/>{agent_id: Tickmark, resource: Coke,<br/>scope: "documents.read vouch.run"}
    Note over W: Step 4 checks the sign-in token<br/>user = oid, firm = tid
    W->>W: 1. scope known and not empty? (else 400)
    W->>DB: 2. agent registered?
    W->>DB: 3. engagement exists in Sara's firm?
    W->>DB: 4. Sara is a real user of that firm?
    W->>DB: 5. ethical wall Sara - Coke?
    W->>DB: 6. Sara is a member of Coke? which role?
    W->>W: 7. every scope allowed for her role?
    W->>DB: INSERT delegated_tokens row (jti, ..., expires +15 min)
    W->>W: sign the pass with the private key
    W-->>T: {access_token: pass, expires_in: 900, scope}
```

The first "no" stops everything with **403** and a reason:

```mermaid
flowchart TD
    S[Request] --> C1{1. scope valid?}
    C1 -->|no| E400[400 invalid_scope]
    C1 -->|yes| C2{2. agent registered?}
    C2 -->|no| D1[403 unknown agent]
    C2 -->|yes| C3{3. engagement in your firm?}
    C3 -->|no| D2[403 engagement not found in your firm]
    C3 -->|yes| C4{4. user exists in firm?}
    C4 -->|no| D3[403 unknown user]
    C4 -->|yes| C5{5. ethical wall?}
    C5 -->|yes| D4[403 ethical wall: reason]
    C5 -->|no| C6{6. member?}
    C6 -->|no| D5[403 not a member]
    C6 -->|yes| C7{7. scopes within role?}
    C7 -->|no| D6[403 Staff may not grant: documents.export]
    C7 -->|yes| OK[200 pass + logbook row]
```

**Role → scopes** (`Warden.Core/Permissions.cs`)

| Role | documents.read | vouch.run | documents.export |
|---|---|---|---|
| Staff | ✅ | ✅ | ❌ |
| Manager | ✅ | ✅ | ✅ |
| Admin | ✅ | ✅ | ✅ |

**What's inside the pass** (`Auth/AgentPass.cs`)

| Claim | Example | Meaning |
|---|---|---|
| `iss` | `warden` | who stamped it |
| `aud` | `warden-tools` | where it may be used (the gate, step 9) |
| `sub` | Sara's id | on whose behalf |
| `act` | `{ "sub": Tickmark's id }` | who is actually acting (RFC 8693) |
| `tid` | Firm A's id | which firm |
| `eng` | Coke's id | the **only** engagement |
| `scope` | `documents.read vouch.run` | the **only** actions |
| `jti` | pass number | same as the logbook row |
| `exp` | +15 minutes | stops working by itself |

Checks 2–7 are the shared decision function (next section); the controller only adds check 1
and the stamping.

**Real results** (dev server, as Sara):

| Sara asks for | Answer |
|---|---|
| Coke, read + vouch | 200, pass, logbook row with lifetime `00:15:00` |
| Coke, export | 403 `Staff may not grant: documents.export` |
| Pepsi | 403 `ethical wall: Works on Coke 2026` |
| Acme (Firm B) | 403 `engagement not found in your firm` |

---

## 7. Checking a pass (step 6c) and why the logbook exists

A signed pass can't be taken back once handed out. The logbook fixes that, so checking a pass
(`AgentPass.Check`, what the gate will run on every tool call) has **two layers**:

```mermaid
flowchart TD
    P[Agent shows a pass] --> L1{Layer 1: the stamp<br/>Warden signature via PUBLIC key?<br/>iss = warden? aud = warden-tools?<br/>not expired, no grace period?}
    L1 -->|no| R1[refuse: bad signature / pass expired /<br/>not meant for the tool gate / not issued by Warden]
    L1 -->|yes| L2{Layer 2: the logbook<br/>jti found in delegated_tokens?<br/>revoked_at empty?}
    L2 -->|no| R2[refuse: pass not in logbook / pass revoked]
    L2 -->|yes| OK[OK + the logbook row<br/>who, agent, engagement, scopes]
```

| Pass shown | Result |
|---|---|
| fresh pass from `/token/exchange` | OK, returns the row |
| one character changed | `bad signature` |
| signed by another key | `bad signature` |
| Sara's sign-in token | refused (not Warden's stamp) |
| Warden-signed but not in the logbook | `pass not in logbook` |
| in the logbook, `revoked_at` set | `pass revoked` |
| expired a minute ago | `pass expired` |

The logbook's whole life:

```mermaid
flowchart LR
    I[Exchange issues pass] -->|writes row| L[(delegated_tokens)]
    G[Gate, step 9] -->|looks up jti| L
    L -->|revoked_at set| X[refuse]
    L -->|revoked_at empty| Y[continue checks]
    R[Revoke / SCIM, step 13] -->|sets revoked_at| L
```

---

## 8. The decision function (step 7)

All "may they?" rules live in **one** place, `Auth/Decision.cs`, so the exchange and the tool
gate can never disagree. Every answer is a `Verdict(Allowed, Reason)`; the reason goes into the
audit log in step 8. The first "no" ends it.

```mermaid
flowchart TD
    subgraph Decide["Decide - used by /token/exchange"]
        A[agent registered?] --> E[engagement in the user's firm?] --> U[user in that firm?]
        U --> W{ethical wall?} --> M{member?} --> R{role allows the scopes?}
    end
    subgraph DecideTool["DecideTool - used by the gate, step 9"]
        T0{tool exists?} --> Decide
        R --> P{tool policy:<br/>agents allowed?} --> S{pass carries<br/>the tool's scope?} --> OK[allowed]
    end
```

**Tool policy** (`Warden.Core/Permissions.cs`)

| Tool | Needs scope | Agents may call? |
|---|---|---|
| `list_documents` | documents.read | ✅ |
| `read_document` | documents.read | ✅ |
| `run_vouch` | vouch.run | ✅ |
| `export_documents` | documents.export | ❌ humans only |

Why `DecideTool` re-runs walls, membership and role instead of trusting the pass: a pass
lives 15 minutes. If a wall goes up or a role changes in that time, the **current** data wins.

| Ask | Verdict |
|---|---|
| Omar (Admin on Pepsi, walled) reads Pepsi | ❌ `ethical wall: Works on Coke 2026` |
| Sara (Firm A) reads Acme (Firm B), Dana (Firm B) reads Coke | ❌ `engagement not found in your firm` |
| Sara's pass (documents.read) → `read_document` on Coke | ✅ `allowed` |
| same pass → `run_vouch` | ❌ `pass does not include vouch.run` |
| Omar's agent → `export_documents` | ❌ `export_documents may not be called by an agent` |
| a pass naming Pepsi, held for Sara | ❌ `ethical wall: ...` |
| `delete_everything` | ❌ `unknown tool: delete_everything` |

---

## 9. Configuration and secrets

```mermaid
flowchart LR
    A[appsettings.json<br/>defaults, committed] --> B[appsettings.Development.json<br/>local overrides, gitignored] --> C[Environment variables<br/>e.g. ConnectionStrings__Warden] --> D[Command line]
    D --> Final[builder.Configuration<br/>later layers win]
```

| Value | Now | Production (later) |
|---|---|---|
| DB connection | `appsettings.json` (local Docker password, not a secret) | env var / Key Vault, or managed identity |
| Dev sign-in key | `appsettings.json`, only trusted in Development | not used (Entra) |
| Warden signing key | generated at startup in Development | `Signing:PrivateKeyPem` from Key Vault |

---

## 10. How it's tested

```mermaid
flowchart TB
    subgraph Collection["xUnit collection 'database' - one at a time, one shared app"]
        DBT[DatabaseTests<br/>direct DbContext,<br/>transaction rolled back]
        TET[TokenExchangeTests<br/>PassCheckTests<br/>DecisionTests]
        SGT[SecurityGuaranteeTests]
    end
    F[WardenFactory<br/>API in memory<br/>migrate + seed warden_test once] --> TET
    F --> SGT
    DBT --> TDB[(warden_test)]
    F --> TDB
    subgraph Parallel[Other classes - no database]
        HT[HealthTests]
        AT[AuthTests]
        SK[SigningKeyTests]
    end
```

- Tests use the **`warden_test`** database; your dev database `warden` is never touched.
- Classes that use the database share one seeded app and take turns (running them in
  parallel caused duplicate-key errors when two seeded at once).
- Every new check was **broken on purpose once** to see its test fail.

| Test file | Proves |
|---|---|
| `HealthTests` | app starts, `/health` is public |
| `DatabaseTests` | classes ↔ tables round-trip; seed runs once |
| `AuthTests` | no / forged / expired / tenant-less tokens rejected; firm from token |
| `SigningKeyTests` | JWKS public, no private fields; Warden-signed pass verifies, forged doesn't |
| `TokenExchangeTests` | good pass; wall, other firm, unknown agent refused; bad scope 400 |
| `PassCheckTests` | real pass OK; tampered, forged, sign-in token, unlogged, revoked refused |
| `DecisionTests` | tool calls: scope on the pass, agents can't export, walls beat the pass, unknown tool |
| `SecurityGuaranteeTests` | `agent_cannot_exceed_user_permissions` ✅, `expired_delegated_token_is_rejected` ✅, `ethical_wall_blocks_even_admin` ✅, `tenant_a_cannot_read_tenant_b` ✅ (others switch on in later steps) |

---

## 11. Handy commands

```bash
cd warden
docker compose up -d                               # Postgres on port 5433
dotnet build                                       # 0 warnings expected (warnings = errors)
dotnet test                                        # all tests
dotnet ef database update -p src/Warden.Api        # apply migrations to dev DB
dotnet ef migrations list -p src/Warden.Api
dotnet run --project src/Warden.Api -- seed        # demo data
dotnet run --project src/Warden.Api -- dev-token sara
dotnet run --project src/Warden.Api --urls http://localhost:5099
docker compose exec postgres psql -U warden        # \dt, \d members, select ...
```

---

## 12. What's next

```mermaid
flowchart LR
    s8[8 audit log] --> s9[9 tool gate] --> s10[10 tool policies] --> s11[11-12 Tickmark through Warden] --> s13[13 revoke + SCIM] --> s14[14 Entra ID] --> s15[15 write-up]
```

Known limits right now:
- The agent only *says* who it is (`agent_id`); proper agent authentication comes later.
- Refused attempts aren't recorded yet (audit log, step 8).
- The decision function exists, but nothing calls `DecideTool` yet: there is no gate until step 9.
