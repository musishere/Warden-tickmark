# Tickmark

Sample audit agent: vouches invoices against an Excel sample, with every tool
call going through [Warden](../warden). The LLM extracts; pass/fail is decided in code.

Scope and status: [docs/MVP.md](docs/MVP.md).

## Layout

```
src/tickmark/          the application (the only code that ships)
  api/                 FastAPI app
  sample/              auditor's Excel sample: loader.py (read) + cleaner.py (keys, flags)
  checks/              deterministic pass/fail checks
  extract/ match/      invoice extraction and sample-to-invoice matching (to do)
  models.py config.py warden_client.py
tools/                 developer tooling, not shipped
  datagen/             synthetic invoices, labels and sample workbooks
  clean_sample.py      write a cleaned workbook (Clean + Issues sheets)
  compare_sample.py    raw cells vs cleaned values, row by row (optionally vs labels)
  verify_clean.py      prove cleaning lost no rows and changed no values
tests/                 pytest, one file per area
evals/                 eval set and scoring (to do)
docs/                  scope and design notes
data/                  local only, gitignored
  external/            sample pack we were given (+ the original zip)
  generated/           output of tools.datagen
  output/              files written by the tools
```

## Run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn tickmark.api.main:app --reload
pytest
```

## Data tools
```bash
python -m tools.datagen.generate --count 20 --seed 1 --messy
python -m tools.clean_sample  data/generated/sample_messy.xlsx data/output/sample_messy_cleaned.xlsx
python -m tools.verify_clean  data/generated/sample_messy.xlsx data/output/sample_messy_cleaned.xlsx
python -m tools.compare_sample data/generated/sample_messy.xlsx --labels data/generated/labels/sample_messy_labels.json
```
