# Tickmark: MVP scope

Goal: a sample agent that vouches invoices against an Excel sample, with
every read and action going through Warden. The LLM extracts; code decides.

## In scope
- [x] Load invoice PDFs from a local folder; Excel sample via openpyxl
- [x] Extraction behind an interface: own text reader + Azure Document Intelligence, and a fake for tests (an LLM only proposes column mappings, it doesn't extract)
- [x] Every extracted value keeps a snip (page, bbox, text)
- [x] Deterministic checks: line items sum, VAT, date in period, duplicates (+ totals reconcile). See how-it-works.md §10
- [x] Match sample rows to invoices (vendor, amount, date, number) → ✅ / ⚠️ / ❓ (+ 👀 needs review). See how-it-works.md §8, §11
- [x] All document access via `WardenClient` (token exchange, invoke); deny on any failure — the agent (`python -m tickmark.agent`, tools in `api/tools.py`); the web app is still a local stand-in
- [ ] `POST /engagements/{id}/vouch` and `GET /engagements/{id}/runs/{runId}`
- [x] Storage: Tickmark's own PostgreSQL (append-only audit trail of runs, documents by hash, results,
  reviews); Docker Compose for local dev. See how-it-works.md §13
- [ ] 20 synthetic labelled invoices, one prompt-injection invoice, eval script (field accuracy, match precision/recall)

## Deferred (after MVP)
- Azure Blob Storage (for the evidence store), Service Bus
- 50–100 invoice eval set, multi-run consistency, CI regression gate
- Credits reservation on runs
- Snip viewer endpoint and Excel add-in
- Azure Container Apps deploy

## Done when
`vouch` on the sample returns correct ✅/⚠️/❓ results, the injection invoice
triggers no export, and a Warden denial stops the run.
