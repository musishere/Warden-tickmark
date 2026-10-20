# Tickmark: MVP scope

Goal: a sample agent that vouches invoices against an Excel sample, with
every read and action going through Warden. The LLM extracts; code decides.

## In scope
- [ ] Load invoice PDFs from a local folder; Excel sample via openpyxl
- [ ] Extraction behind an interface: Azure Document Intelligence + LLM, and a fake for tests
- [ ] Every extracted value keeps a snip (page, bbox, text)
- [ ] Deterministic checks: line items sum, VAT, date in period, duplicates
- [ ] Fuzzy match of sample rows to invoices (vendor, amount, date) → ✅ / ⚠️ / ❓
- [ ] All document access via `WardenClient` (token exchange, invoke); deny on any failure
- [ ] `POST /engagements/{id}/vouch` and `GET /engagements/{id}/runs/{runId}`
- [ ] Storage: local JSON/SQLite file, no external database
- [ ] 20 synthetic labelled invoices, one prompt-injection invoice, eval script (field accuracy, match precision/recall)

## Deferred (after MVP)
- MongoDB, Azure Blob Storage, Service Bus
- 50–100 invoice eval set, multi-run consistency, CI regression gate
- Credits reservation on runs
- Snip viewer endpoint and Excel add-in
- Azure Container Apps deploy

## Done when
`vouch` on the sample returns correct ✅/⚠️/❓ results, the injection invoice
triggers no export, and a Warden denial stops the run.
