# Tickmark: progress and pipeline

What is built so far, how the pieces fit together, and how to check the results yourself.
Scope and goals: [MVP.md](MVP.md).

## Pipeline

```mermaid
flowchart LR
    subgraph IN["Inputs"]
        X[/"Auditor sample<br/>(.xlsx)"/]
        P[/"Invoice PDFs<br/>(folder)"/]
    end

    subgraph S1["1 · Load + clean"]
        L["loader.py<br/>read rows, parse amounts & dates<br/>errors + skipped reported"]
        C["cleaner.py<br/>vendor key, flags<br/>(row never modified)"]
    end

    subgraph S2["2 · Read PDFs"]
        A["Azure Document Intelligence<br/>prebuilt-invoice"]
        K[("cache<br/>data/cache/docint/&lt;sha256&gt;.json")]
        F["LabelExtractor<br/>(test only: perfect reading)"]
    end

    subgraph S3["3 · Identify"]
        T["trust filter<br/>confidence ≥ 0.90<br/>total = subtotal + tax"]
        M["rules 2–5<br/>number + 1 field, or vendor + amount/date<br/>clear winner, one PDF per row"]
    end

    subgraph S5["4 · Verify the claim"]
        Q["vouch: row vs linked PDF<br/>(vendor, amount, date, number)<br/>+ invoice checks: totals, lines, VAT, period, duplicates"]
    end

    subgraph OUT["Outputs"]
        R["✅ matched / ⚠️ difference / 👀 needs_review / ❓ no_evidence<br/>+ which PDF, every comparison, check and step"]
        W[/"workbook<br/>Identified · Steps · Comparisons · PDF fields"/]
    end

    subgraph S4["5 · Audit trail (--save)"]
        DB[("Postgres (append-only, row-level security per firm + engagement)<br/>runs · documents · extractions · sample_rows<br/>sample_issues · match_results · reviews")]
        ES[("evidence store<br/>data/store/&lt;firm&gt;/&lt;engagement&gt;/&lt;sha256&gt;")]
        H["human review<br/>tools.review"]
    end

    X --> L --> C --> M
    P --> A <--> K
    P -. test mode .-> F
    A --> T
    F --> T
    T --> M --> Q --> R --> W
    R --> DB
    X -. copy by hash .-> ES
    P -. copy by hash .-> ES
    H --> DB
    DB --> SR["show_run · verify_run"]
    ES -.-> SR

    V{{"verify_clean<br/>nothing lost, nothing changed"}}
    E{{"eval_identify<br/>wrong links must be 0"}}
    EV{{"eval_vouch<br/>false ✅ must be 0"}}
    L -.-> V
    C -.-> V
    R -.-> E
    R -.-> EV
```

Azure only **reads** the PDF (values, confidence, position). Every decision is made by plain code in
`tickmark.match.identify`: the same rules whichever reader is used, with a trace for every decision.

The same flow as plain text:

```
sample.xlsx ─► load ─► clean ─────────────────────────────┐
                 │        (vendor key, flags)             │
                 └─► errors / skipped rows (reported)     ▼
invoice PDFs ─► Azure DI ⇄ cache ─► trust filter ─► identify (rules) ─► vouch (row vs PDF + checks)
                                                                         ─► ✅ / ⚠️ / 👀 / ❓
            └─(test) LabelExtractor ─┘                      │
                                                            └─► trace: steps + comparisons ─► workbook
                                                            │
                                    --save ─► Postgres (append-only) + evidence store (files by SHA-256)
                                                 ▲                         │
                                       tools.review (human)       show_run · verify_run
```

## What is covered

### 1. Loading the auditor's sample: `src/tickmark/sample/loader.py`
- Finds the header row and maps columns by alias, ignoring case, punctuation and suffixes like
  `(EUR)`: `Row ID`, `Vendor`/`Supplier`, `Amount`/`Total`, `Date`/`Invoice Date`,
  `Invoice No`/`Reference`.
- Row ids are kept exactly as written (`S-01`). The Excel row number is used only when the id is empty.
- Amounts: `1.234,50`, `€ 2,904.10`, `(50.00)` and `123.45-` are all read correctly, as exact decimals.
- Dates: Excel dates, `YYYY-MM-DD` and `DD.MM.YYYY`. Ambiguous `03/04/2025` is rejected, not guessed.
- Text: removes zero-width characters, turns non-breaking spaces into spaces, collapses whitespace.
- Blank rows are ignored. Footer rows (`Total`, `Subtotal`, `Notes`, …) are skipped and reported.
  Bad rows are reported as errors with a reason. Nothing disappears silently.

### 2. Cleaning: `src/tickmark/sample/cleaner.py`
- Never changes a row. It only adds derived values:
  - `vendor_key`: `Acme B.V.`, `ACME BV` and `acme  b.v` all become `acme`, and
    `Müller Büro GmbH` becomes `muller buro`.
  - flags (each with a note): `duplicate_of:<row>`, `negative_amount`, `zero_amount`,
    `date_outside_period`.

### 3. Proving nothing was lost or changed: `tools/verify_clean.py`
Compares the output directly with the raw Excel cells, without using the loader's own logic:
- **completeness**: every non-blank row ends up exactly once as loaded, an error or skipped
- **fidelity**: vendor differs only in whitespace; amount has the same digits, decimals and sign;
  date and row id are equal
- **untouched**: the cleaner returns every row unmodified and in order
- **control total**: the sum of numeric amounts before and after
- **round trip**: the written workbook holds exactly the loaded rows and issues

Tests deliberately tamper with the output (drop a row, shift an amount, change a vendor or date,
count a row twice) and check that the verifier catches every one.

### 4. Reading PDFs: `src/tickmark/extract/`
- `DocIntExtractor` (`azure_docint.py`): Azure `prebuilt-invoice`. It reads vendor, invoice number,
  date, total, subtotal and tax, each with a confidence and a snip (page, position in points, text).
  - Record/replay cache: each PDF is sent to Azure **once**, the raw result is stored under its
    SHA-256, and later runs work offline.
  - Without credentials it fails with a clear message. It never guesses.
- `LabelExtractor` (`fake.py`): reads the ground-truth label JSON. "Perfect reading", used to test
  the matching rules on their own. Never use it as evidence.

### 5. Identifying which PDF a row is about: `src/tickmark/match/identify.py`
Precision over coverage: **a wrong link is the one outcome that must never happen**.

| # | Rule |
|---|------|
| 1 | Only **trusted** values count: confidence ≥ 0.90 and parseable. The total counts only if subtotal + tax = total. |
| 2 | **Invoice number** equal **+ at least one more field** → can link. Number alone, or a different number → review. |
| 3 | No usable number: **vendor equal + (amount or date equal)** → can link. A vendor that is only *similar* → review, never a link. |
| 4 | **Clear winner**: the best PDF agrees on more fields than any other, otherwise review. |
| 5 | **One PDF backs one row**: rows contending for a PDF → only a strictly better row keeps it. |

Field comparison: number ignores spaces, `-`, `/`, `.` and case; vendor uses the normalised key;
amount is within 0.01; date is the same day.

Outcome per row: 🔗 `linked` (with evidence and snips), 👀 `needs_review` (with the candidates and
why), ❓ `no_evidence`.

### 6. Seeing the whole process: `tools/identify_sample.py`
- `--trace` prints what was read from each PDF (and what is not trusted, and why), each row compared
  field by field with every PDF that agrees on anything, and the numbered decision steps.
- `--out file.xlsx` writes four sheets, with links that open each PDF:

| Sheet | Contents |
|---|---|
| Identified | one line per row: status, PDF, evidence or reason |
| Steps | how each row was decided, step by step, with the rule that decided |
| Comparisons | every row × every PDF, field by field: row value, PDF value, result |
| PDF fields | everything read from each PDF: confidence, used or why not, page and position |

### 7. Measuring accuracy: `tools/eval_identify.py`, `tools/datagen/hard.py`
Three datasets:
- **generated**: 16 rows, 20 PDFs, no invoice numbers, decoy vendors
- **external**: the 7-row sample pack, with invoice numbers
- **hard**: 11 rows built to fool a matcher: typo'd number, number reused by two vendors, same
  vendor and amount on two dates, same vendor and date with two amounts, spelling variant,
  lookalike vendor, decoy vendor with the same amount and date, duplicate rows

Current results with **perfect reading (`--labels`)**, which tests the rules only:

| Dataset | Rows | Correct links | Review | No evidence | **Wrong links** | Safe outcome |
|---|---|---|---|---|---|---|
| generated | 16 | 12 | 0 | 4 | **0** | 16/16 |
| external | 7 | 6 | 0 | 1 | **0** | 7/7 |
| hard | 11 | 4 | 6 | 1 | **0** | 11/11 |

In the hard set, the one "missed" row is H-04 (a typo'd invoice number), which goes to review by design.
The eval exits with an error on any wrong link, so it can gate CI.

### 8. Verifying the claim: `src/tickmark/vouch/`, `tools/vouch_sample.py`
Each Excel row is a claim: "there is an invoice from vendor X, number N, dated D, for amount Y".
Once the row's PDF is known, code checks whether the PDF backs that claim and whether the invoice
itself is sound:

| Part | What is checked | Result per item |
|---|---|---|
| **Row vs PDF** | vendor (normalised name), amount (vs the **printed** total), date, invoice number (if the row has one) | agrees / differs (with the exact difference, e.g. `+250.00`, `+3 days`) / unverifiable (not read, or confidence < 0.90) |
| **totals_reconcile** | subtotal + tax = total | pass / fail / not_checkable |
| **lines_sum** | line amounts sum to the subtotal, and each line's qty × unit price = amount | pass / fail / not_checkable |
| **vat_rate** | tax is an allowed rate of the subtotal (0 / 9 / 21 %) | pass / fail / not_checkable |
| **in_period** | invoice date inside the engagement's audit period | pass / fail / not_checkable |
| **duplicate** | same vendor + number on another PDF → fail; same number from another vendor → warn | pass / fail / warn |

Verdict per row:

| Verdict | When |
|---|---|
| ✅ **matched** | every field agrees and every check passes (warnings are shown, not blocking) |
| ⚠️ **difference** | any field differs or any check fails: each one listed with its amount or reason |
| 👀 **needs_review** | which PDF is not settled, or something could not be verified (never ✅ on missing evidence) |
| ❓ **no_evidence** | no PDF for this row |

Any difference over 0.01 is ⚠️; the auditor judges materiality. The audit period comes from the
engagement (`tools.tenants set-period`), or `--period` without the database.

Results with perfect reading (`python -m tools.eval_vouch --labels`):

| Dataset | Verdicts as labelled | False ✅ | Notes |
|---|---|---|---|
| external | 7/7 | 0 | equal to the pack's `expected_results.json`: S-02 +250.00, S-03 +3 days, S-06 totals don't add up |
| generated | 16/16 | 0 | rows 1 and 13 +25.00; rows 4 / 11 / 14 from their invoice's defect (lines / VAT / period) |
| hard | 11/11 | 0 | the 4 linked rows ✅, the rest stay 👀 / ❓ |

Over all 20 generated PDFs the checks find every labelled defect (lines 2, VAT 3, period 2,
duplicate 2) with no false alarms. With a deliberately loose 1000 tolerance the eval reports
false ✅ and fails, so it does catch a weakened rule.

### 9. Audit trail in Postgres: `src/tickmark/store/`, `migrations/`
In audit the result is half of it: you must also show, months later, **what was decided, from
which exact files, under which rules, and what a human did with it**. With `--save`, a run is
stored completely (one transaction) in Tickmark's own Postgres database. Warden keeps its own
database; tenant and engagement ids are Warden's.

| Table | Holds |
|---|---|
| `tenants` | audit firms (Warden's id, name, Entra tenant id). Registry only, written by the owner role |
| `engagements` | the clients each firm audits (Warden's id, firm, client name, audit period). Registry only |
| `runs` | when, who, engagement, matcher version (git commit), thresholds, extractor + model, completed/failed + error |
| `documents` | every file by **SHA-256** (sample and PDFs), size, pages, path in the evidence store |
| `run_documents` | which PDFs this run compared against, and under which file name |
| `extractions` | what the extractor read: raw result (Azure JSON) + parsed fields with confidence and snips |
| `sample_rows` | each loaded row exactly as loaded, plus the raw Excel cells, vendor key and flags |
| `sample_issues` | rows that errored or were skipped, with their raw cells, so no row disappears |
| `verdicts` | one per row: ✅ / ⚠️ / 👀 / ❓, the PDF checked, every field agreement, every invoice check, reasons, steps |
| `match_results` | one per row: status, linked PDF and page, evidence, candidates, reason, **full trace** (comparisons + steps) |
| `reviews` | human decisions (accept, choose_candidate + PDF, reject, requested_from_client), who, when, note |

Rules:
- **Append-only, enforced by Postgres**: a trigger rejects UPDATE, DELETE and TRUNCATE on every
  table. A re-run is a new run. A human decision is a new review, and the latest one is current.
- **Linked by hash and id, not by file name**: the same bytes under two names are one document.
- **Evidence store**: each file used is copied once to
  `data/store/<tenant>/<engagement>/<sha[:2]>/<sha256>.<ext>` and never overwritten. `verify_run`
  re-hashes it. (It stands in for Azure Blob Storage.)
- **The rules stay in code**: the database records what happened; `tickmark.match` decides.
- The schema is managed by Alembic (`migrations/`); `alembic check` confirms the code and the
  migrations agree.

#### Multi-tenant: many audit firms, many clients
**Tenant = audit firm** (from the verified Entra token in production). **Engagement = one client
company** the firm audits. Every audit row carries `tenant_id` + `engagement_id`, and Postgres
enforces the separation, not just the code:

| Layer | What it does |
|---|---|
| **Row-level security** on all 11 tables | A session sees and can insert only rows of the tenant **and** engagement it set (`app.tenant_id`, `app.engagement_id`, transaction-local). **Fail closed**: nothing set → nothing visible, nothing writable. |
| **Composite foreign keys** `(tenant_id, engagement_id, id)` | A result, review or file link can only point at rows of the same engagement, even with forged ids. |
| **Two database roles** | `tickmark` (owner) runs migrations and registers firms and engagements. The app connects as **`tickmark_app`**: `SELECT` + `INSERT` only, not the owner, so it cannot update, delete, truncate, drop triggers or switch off row-level security. |
| **Per-engagement files** | The same PDF in two engagements is stored twice (separate documents, separate evidence folders), so an ethical wall (Coke vs Pepsi in one firm) also holds for storage. |
| **"No run", not "forbidden"** | A run of another firm or engagement looks like it does not exist. |

In code, every store call takes a `TenantContext(tenant_id, engagement_id, user)`
(`src/tickmark/store/context.py`); `scoped()` sets it at the start of the transaction. The
tests (`tests/test_tenancy.py`) include `tenant_a_cannot_read_tenant_b`, the ethical wall,
fail-closed, forged ids, cross-engagement links, and everything the app role must not be able to do.

### 10. Project layout
```
src/tickmark/   app: api/, sample/ (loader, cleaner), extract/, match/, vouch/, store/, checks/, pipeline.py, models.py
tools/          datagen/, clean_sample, compare_sample, verify_clean, identify_sample, eval_identify,
                vouch_sample, eval_vouch, show_run, review, verify_run, tenants (register firms + engagements)
migrations/     Alembic: 0001 audit tables + append-only triggers, 0002 multi-tenant (RLS, roles), 0003 verdicts
docker/initdb/  creates the tickmark_app login role on a fresh dev database
tests/          105 tests (32 need Postgres and skip cleanly without it)
data/           external/ (given), generated/, output/, cache/, store/   (gitignored)
docs/           MVP.md, PROGRESS.md
```

## How to run

```bash
cd tickmark
source .venv/bin/activate          # once per terminal; the prompt shows (.venv)

pytest                                               # all tests, offline
python -m tools.verify_clean  data/external/tickmark-sample/sample.xlsx
python -m tools.eval_identify --labels               # rules only (perfect reading)
python -m tools.eval_vouch --labels                  # verdicts: false ✅ must be 0
python -m tools.vouch_sample data/external/tickmark-sample/sample.xlsx data/external/tickmark-sample/invoices \
    --labels data/external/tickmark-sample/labels --period 2026-01-01:2026-12-31 --trace --out data/output/vouched.xlsx
python -m tools.eval_identify                        # with Azure (needs .env)
python -m tools.identify_sample data/external/tickmark-sample/sample.xlsx \
    data/external/tickmark-sample/invoices --trace --out data/output/identified.xlsx
```

Audit database (Tickmark's own Postgres, host port 5435):

```bash
docker compose up -d                 # start Postgres
alembic upgrade head                 # create / upgrade the tables

# register an audit firm and a client engagement (owner role); prints their ids
python -m tools.tenants add-tenant "Firm A"
python -m tools.tenants add-engagement <tenant id> "Coke" --period 2026-01-01:2026-12-31
python -m tools.tenants list

# act inside one firm + engagement (or pass --tenant / --engagement each time)
export TICKMARK_TENANT=<tenant id> TICKMARK_ENGAGEMENT=<engagement id>
python -m tools.vouch_sample data/external/tickmark-sample/sample.xlsx \
    data/external/tickmark-sample/invoices --save                        # prints the run id
python -m tools.show_run                                 # this engagement's runs
python -m tools.show_run RUN_ID --trace --out data/output/run.xlsx       # rebuilt from the DB only
python -m tools.review RUN_ID S-02 --decision accept --note "checked"    # reviewer = --user (OS user)
python -m tools.verify_run RUN_ID                        # files unchanged, every row accounted for
```

Azure credentials go in `tickmark/.env`:
```
TICKMARK_DOCINT_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
TICKMARK_DOCINT_KEY=<key>
```

## How to check results by hand
Two separate questions:
1. **Was the PDF read correctly?** Compare the *PDF fields* sheet with the PDF (click the link).
2. **Was the decision right?** For a row, read *Steps*, then filter *Comparisons* by that row and
   `Result = equal` to confirm that no other PDF matched as well.
3. **Months later:** `show_run RUN_ID --trace` shows the same thing from the database, with the
   reviews. `verify_run RUN_ID` proves the files behind it are the same bytes.

## Not done yet

| Item | Status |
|---|---|
| Live Azure run | Needs credentials. Then measure reading accuracy and replace the hand-written test fixture `tests/fixtures/docint_inv_2026_1222.json` with a real cached result. |
| Materiality | A per-engagement "clearly trivial" threshold; today any difference over 0.01 is ⚠️. |
| VAT rates | Per-country rate tables; today 0 / 9 / 21 % for every engagement. |
| Duplicates across runs | Duplicates are checked within one run, not yet against the engagement's earlier runs. |
| Reviewing verdicts | Reviews are recorded on the identification; a review step for verdicts is next. |
| Warden | PDF reads are not yet routed through `WardenClient` (fail closed). |
| API | `POST /engagements/{id}/vouch` and `GET …/runs/{runId}` are not built yet. They will read from the audit store. |
| Review UI | Reviews are recorded with `tools.review`; no screen yet. |
| Tenant from the token | The API must take tenant and engagement from the Warden-verified token, never from the request. The CLI flags are stand-ins. |
| Registry sync | `tenants` / `engagements` are registered by hand; sync them from Warden. |
| Retention / deletion | Append-only plus per-firm deletion requests need a legal-hold design. |
| Evidence storage | Move the evidence store to Azure Blob Storage, a container per tenant, with immutability. |
| Larger eval set | 34 rows is too few to trust a "100%". Grow it towards the 50–100 invoices planned in MVP.md. |
