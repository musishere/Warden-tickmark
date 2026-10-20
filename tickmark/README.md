# Tickmark

Sample audit agent: vouches invoices against an Excel sample, with every tool
call going through [Warden](../warden). The LLM extracts; pass/fail is decided in code.

Scope: [docs/MVP.md](docs/MVP.md). What is built, pipeline diagram and how to verify:
[docs/PROGRESS.md](docs/PROGRESS.md).

## Layout

```
src/tickmark/          the application (the only code that ships)
  api/                 FastAPI app
  sample/              auditor's Excel sample: loader.py (read) + cleaner.py (keys, flags)
  checks/              deterministic pass/fail checks
  extract/             read invoice PDFs (Azure Document Intelligence; labels for tests)
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

## Does the PDF back the row's claim?
`tickmark.vouch` compares the row's vendor, amount, date and invoice number with its linked PDF and
runs the invoice's own checks (totals, lines, VAT rate, audit period, duplicates). Any difference
over 0.01 is ⚠️; anything that cannot be verified is 👀, never ✅. See [docs/PROGRESS.md](docs/PROGRESS.md) §8.
```bash
python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --period 2026-01-01:2026-12-31 --trace --out data/output/vouched.xlsx
python -m tools.eval_vouch --labels
```

## Audit trail (multi-tenant)
`--save` stores a run in Postgres (append-only): the files by hash, what was read, every row,
every result with its full trace, and human reviews. Every row belongs to one **audit firm
(tenant)** and one **client engagement**; Postgres row-level security keeps them apart, and the
app connects as `tickmark_app` (read + append only). See [docs/PROGRESS.md](docs/PROGRESS.md) §9.
```bash
python -m tools.tenants add-tenant "Firm A"              # prints the tenant id
python -m tools.tenants add-engagement <tenant id> "Coke" --period 2026-01-01:2026-12-31
export TICKMARK_TENANT=<tenant id> TICKMARK_ENGAGEMENT=<engagement id>

python -m tools.vouch_sample SAMPLE.xlsx INVOICE_DIR --save
python -m tools.show_run RUN_ID --trace --out data/output/run.xlsx
python -m tools.review RUN_ID S-02 --decision accept
python -m tools.verify_run RUN_ID
```
