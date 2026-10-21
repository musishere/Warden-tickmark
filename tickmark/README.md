# Tickmark

Sample audit agent: vouches invoices against an Excel sample, with every tool
call going through [Warden](../warden). The LLM extracts; pass/fail is decided in code.

Scope: [docs/MVP.md](docs/MVP.md). What is built, pipeline diagram and how to verify:
[docs/PROGRESS.md](docs/PROGRESS.md).

## Layout

```
src/tickmark/          the application (the only code that ships)
  api/                 FastAPI app + the page (static/) + background runs with their log
  sample/              auditor's Excel sample: loader.py (read) + cleaner.py (keys, flags)
  checks/              deterministic pass/fail checks
  extract/             read invoice PDFs: own text first (local.py), Azure only where needed (router.py)
  match/               which PDF each row is about (identify.py)
  vouch/               does that PDF back the row's claim: ✅ / ⚠️ / 👀 / ❓
  store/               append-only audit trail in Postgres + evidence store
  pipeline.py models.py config.py warden_client.py
tools/                 developer tooling, not shipped
  datagen/             synthetic invoices, labels and sample workbooks
  clean_sample.py      write a cleaned workbook (Clean + Issues sheets)
  compare_sample.py    raw cells vs cleaned values, row by row (optionally vs labels)
  verify_clean.py      prove cleaning lost no rows and changed no values
  identify_sample.py   row -> PDF, with --trace, --out workbook, --save to the audit DB
  eval_identify.py     wrong links (must be 0), per dataset
  vouch_sample.py      row -> PDF -> verdict, with --trace, --out workbook, --save
  eval_vouch.py        verdicts vs labels, defects found per check (false ✅ must be 0)
  show_run.py review.py verify_run.py   read a stored run, add a human review, verify evidence
  tenants.py           register audit firms (tenants) and client engagements
  map_columns.py       which column means what, once per client (an LLM can suggest, incl. new meanings)
  compare_readers.py   local reading vs Azure on the same PDFs (calibration; needs the Azure key)
migrations/            Alembic: audit tables, append-only triggers, multi-tenant row-level security
docker/initdb/         creates the tickmark_app database role (dev)
tests/                 pytest, one file per area
evals/                 eval set and scoring (to do)
docs/                  scope and design notes
data/                  local only, gitignored
  external/            sample pack we were given (+ the original zip)
  generated/           output of tools.datagen
  output/              files written by the tools
  cache/ store/        Azure result cache; evidence store (files by SHA-256)
```

## Run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d && alembic upgrade head    # Tickmark's Postgres on port 5435
uvicorn tickmark.api.main:app --reload
pytest                                           # DB tests skip if Postgres is not running
```

## Web app (whole flow, with its log)
```bash
uvicorn tickmark.api.main:app --reload       # then open http://localhost:8000
tail -f data/logs/tickmark.log               # the same step-by-step log the page shows
```
Upload a sample and its PDFs → settle each column's meaning (known names, "Ask the LLM", or by
hand, incl. meanings of your own) → run (period, currency, Azure auto/off/always, item lines) →
watch every step as it happens, then the verdicts with each field and check, and how each PDF was
read. With a firm + engagement chosen, the mapping and the run are saved to the audit trail, past
runs open again and reviews can be recorded. The firm/engagement come from the page here: a local
stand-in for Warden; there is no login.

## Data tools
```bash
python -m tools.datagen.generate --count 20 --seed 1 --messy
python -m tools.clean_sample  data/generated/sample_messy.xlsx data/output/sample_messy_cleaned.xlsx
python -m tools.verify_clean  data/generated/sample_messy.xlsx data/output/sample_messy_cleaned.xlsx
python -m tools.compare_sample data/generated/sample_messy.xlsx --labels data/generated/labels/sample_messy_labels.json
```

## Which PDF is each row about?
`tickmark.match.identify` links a row to a PDF only on strong, unique, corroborated evidence:
invoice number + one more field, or vendor + amount/date; a clear winner; one PDF per row.
Anything weaker is `needs_review` with the candidates, never a guess.
```bash
python -m tools.datagen.hard                 # hard negatives -> data/generated/hard/
python -m tools.eval_identify --labels       # matching alone (perfect extraction from labels)
python -m tools.eval_identify                # with Azure Document Intelligence (needs .env)
python -m tools.identify_sample data/external/tickmark-sample/sample.xlsx \
    data/external/tickmark-sample/invoices --out data/output/identified.xlsx
```
Azure results are cached in `data/cache/docint/` (one call per PDF, ever).

## Reading the PDFs: own text first, Azure only where needed
Each PDF is read from its own text (free). It goes to Azure only when it cannot be read that way
(a scan or photo, text over a full-page image, unreadable or rotated text) or when a value a row
needs was not found clearly (no label, two different totals, a number shared with another invoice
whose vendor is unknown). A value read differently by both is trusted by neither. `--azure off`
never calls Azure: those rows stay 👀/❓ with the reason. The item-lines check needs Azure for every
linked invoice, so it is off unless the engagement turns it on (`tools.tenants set-line-items`).
Each stored run records how every PDF was read and why Azure was needed.
```bash
python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --azure off      # never Azure
python -m tools.eval_vouch --azure off --no-line-items                 # own text only, on the test sets
python -m tools.compare_readers INVOICE_DIR                            # local vs Azure (needs the key)
```

## Does the PDF back the row's claim?
`tickmark.vouch` compares every column the mapping gave a meaning (vendor, amount, currency, date,
invoice number, net, VAT, PO, the layout's own meanings) with its linked PDF and runs the invoice's
own checks (totals, item lines if on, VAT rate, audit period, duplicates). Any
difference over 0.01 is ⚠️; anything that cannot be verified is 👀, never ✅. See [docs/PROGRESS.md](docs/PROGRESS.md) §8.
The row's currency comes from a currency column, else `(XXX)` in the amount header (`Amount (USD)`),
else the engagement's currency (`--currency`, or `tools.tenants set-currency`). The PDF's comes from
the invoice total. No currency on either side → 👀.
```bash
python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --period 2026-01-01:2026-12-31 --currency EUR --trace --out data/output/vouched.xlsx
python -m tools.eval_vouch --labels
```

## Any spreadsheet layout
No column is required. Each column gets a meaning: a known one (invoice number, vendor, amount,
date, net, VAT, PO, currency, …) or one the layout adds (`--meaning cost_centre=text:"Cost centre"`).
A run only needs enough to identify an invoice: the invoice number plus one more column, or the
vendor plus the amount or date. Known headers map automatically, unless two columns could hold the
same meaning ("Amount" and "Total"): then it is never guessed and the run waits for a choice. For
anything else, map the columns once per client; the mapping is saved and reused for that header
layout. An LLM (any OpenAI-compatible API, OpenRouter in `.env.example`) can suggest it, including
new meanings, from headers and value patterns (no values leave unless you separately allow it). Title rows above the header are found and kept. Text amounts follow the
column's own decimal mark; one that reads two ways (`1.234`) is refused, never guessed.
See [docs/PROGRESS.md](docs/PROGRESS.md) §9.
```bash
python -m tools.map_columns SAMPLE.xlsx                                   # what is known, what is missing
TICKMARK_LLM_MAPPING=on python -m tools.map_columns SAMPLE.xlsx --suggest # the LLM suggests (patterns only)
python -m tools.map_columns SAMPLE.xlsx --set amount=D --set vendor=B --set invoice_date=C --confirm  # save
python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --save              # uses the saved mapping
```

## Audit trail (multi-tenant)
`--save` stores a run in Postgres (append-only): the files by hash, what was read, every row,
every result with its full trace, and human reviews. Every row belongs to one **audit firm
(tenant)** and one **client engagement**; Postgres row-level security keeps them apart, and the
app connects as `tickmark_app` (read + append only). See [docs/PROGRESS.md](docs/PROGRESS.md) §10.
```bash
python -m tools.tenants add-tenant "Firm A"              # prints the tenant id
python -m tools.tenants add-engagement <tenant id> "Coke" --period 2026-01-01:2026-12-31 --currency EUR
export TICKMARK_TENANT=<tenant id> TICKMARK_ENGAGEMENT=<engagement id>

python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --save
python -m tools.show_run RUN_ID --trace --out data/output/run.xlsx
python -m tools.review RUN_ID S-02 --decision accept
python -m tools.verify_run RUN_ID
```
