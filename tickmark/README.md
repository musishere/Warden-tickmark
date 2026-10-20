# Tickmark

Sample audit agent: vouches invoices against an Excel sample, with every tool
call going through [Warden](../warden). The LLM extracts; pass/fail is decided in code.

Scope and status: [MVP.md](MVP.md).



## Run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn tickmark.api.main:app --reload
pytest
```
