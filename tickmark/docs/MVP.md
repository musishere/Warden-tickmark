# Tickmark: MVP scope

Goal: a sample agent that vouches invoices against an Excel sample, with
every read and action going through Warden. The LLM extracts; code decides.

## In scope
- [ ] Load invoice PDFs from a local folder; Excel sample via openpyxl
- [ ] Extraction behind an interface: Azure Document Intelligence + LLM, and a fake for tests
- [ ] Every extracted value keeps a snip (page, bbox, text)
- [x] Deterministic checks: line items sum, VAT, date in period, duplicates (+ totals reconcile). See PROGRESS.md §8
- [x] Match sample rows to invoices (vendor, amount, date, number) → ✅ / ⚠️ / ❓ (+ 👀 needs review). See PROGRESS.md §5, §8
- [ ] All document access via `WardenClient` (token exchange, invoke); deny on any failure
- [ ] `POST /engagements/{id}/vouch` and `GET /engagements/{id}/runs/{runId}`
- [x] Storage: Tickmark's own PostgreSQL (append-only audit trail of runs, documents by hash, results,
  reviews); Docker Compose for local dev. See PROGRESS.md §9
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
