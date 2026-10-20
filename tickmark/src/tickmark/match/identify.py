"""Decide which invoice PDF each sample row is about. Pure code: no I/O, no LLM.

Precision over coverage: a row is linked to a PDF only when the evidence is strong, unique and
corroborated. Anything less is needs_review (with the candidates) or no_evidence. A wrong link
would put a PDF behind a row it does not support, which is the one outcome this must never produce.

Rules, in order:
  1. Only trusted extracted fields count: confidence >= CONFIDENCE_MIN and parseable. The total
     counts only if subtotal + tax reconciles to it.
  2. Invoice number equal + at least one more field equal -> can link. Number alone -> review.
  3. Without a usable number: vendor equal + (amount or date equal) -> can link.
     A vendor that is only similar never links (lookalike vendors) -> review.
  4. The best candidate must have strictly more agreeing fields than any other, else review.
  5. One PDF backs one row: if rows contend for a PDF, only a strictly better one keeps it.

Every decision is recorded as it is made (Identification.comparisons and .steps), so a person can
check each link without re-running anything.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz

from tickmark.models import (
    Candidate,
    CleanRow,
    Comparison,
    ExtractedInvoice,
    FieldCheck,
    Identification,
    PdfField,
    Snip,
)
from tickmark.sample import normalize_vendor

CONFIDENCE_MIN = 0.90
VENDOR_SIMILAR = 90  # rapidfuzz token_sort_ratio on vendor keys: similar enough to review
AMOUNT_TOL = Decimal("0.01")
# Recorded with every stored run, so an old result can be explained after the rules change.
THRESHOLDS = {"confidence_min": CONFIDENCE_MIN, "vendor_similar": VENDOR_SIMILAR, "amount_tol": str(AMOUNT_TOL)}


def normalize_number(text: str) -> str:
    """'inv-2026 / 1037' and 'INV20261037' compare equal."""
    return re.sub(r"[\s\-/._#]", "", text).upper()


@dataclass
class _Pdf:
    file: str
    vendor_key: str | None = None
    vendor: str | None = None
    number: str | None = None
    raw_number: str | None = None
    invoice_date: date | None = None
    total: Decimal | None = None
    snips: dict[str, Snip] = field(default_factory=dict)
    fields: dict[str, PdfField] = field(default_factory=dict)


@dataclass
class _Cand:
    pdf: _Pdf
    agree: list[str]
    eligible: bool
    note: str
    rank: tuple


def pdf_fields(inv: ExtractedInvoice) -> list[PdfField]:
    """What was read from a PDF and whether each value may be used as evidence (and why not)."""
    return list(_trusted(inv).fields.values())


def _trusted(inv: ExtractedInvoice) -> _Pdf:
    pdf = _Pdf(file=inv.file)
    for name, f in inv.fields.items():
        why = ""
        if f.confidence is None:
            why = "no confidence reported"
        elif f.confidence < CONFIDENCE_MIN:
            why = f"confidence {f.confidence:.2f} < {CONFIDENCE_MIN}"
        elif not f.value.strip():
            why = "empty"
        pdf.fields[name] = PdfField(name=name, value=f.value, confidence=f.confidence,
                                    trusted=not why, why_not=why, snip=f.snip)
        if f.snip:
            pdf.snips[name] = f.snip

    def value(name: str) -> str | None:
        f = pdf.fields.get(name)
        return f.value.strip() if f and f.trusted else None

    def distrust(name: str, why: str) -> None:
        if name in pdf.fields and pdf.fields[name].trusted:
            pdf.fields[name] = pdf.fields[name].model_copy(update={"trusted": False, "why_not": why})

    def money(name: str) -> Decimal | None:
        try:
            return Decimal(value(name)) if value(name) else None
        except InvalidOperation:
            distrust(name, "not a number")
            return None

    if v := value("vendor"):
        pdf.vendor, pdf.vendor_key = v, normalize_vendor(v)
    if n := value("invoice_number"):
        pdf.raw_number, pdf.number = n, normalize_number(n) or None
    if d := value("invoice_date"):
        try:
            pdf.invoice_date = date.fromisoformat(d)
        except ValueError:
            distrust("invoice_date", "not a date")
    total, sub, tax = money("total"), money("subtotal"), money("tax")
    if total is not None:
        if sub is None or tax is None:
            distrust("total", "cannot reconcile: subtotal or tax not read")
        elif abs(sub + tax - total) > AMOUNT_TOL:
            distrust("total", f"subtotal {sub} + tax {tax} = {sub + tax} ≠ total {total}")
        else:
            pdf.total = total
    return pdf


def _shown(pdf: _Pdf, name: str) -> str:
    f = pdf.fields.get(name)
    return f.value if f else ""


def _check_field(pdf: _Pdf, name: str, row_value: str, ok: bool, detail_if_differs: str = "") -> FieldCheck:
    f = pdf.fields.get(name)
    label = {"invoice_number": "number", "vendor": "vendor", "total": "amount", "invoice_date": "date"}[name]
    if f is None:
        return FieldCheck(field=label, row_value=row_value, pdf_value="", result="missing")
    if not f.trusted:
        return FieldCheck(field=label, row_value=row_value, pdf_value=f.value, result="untrusted",
                          detail=f.why_not)
    return FieldCheck(field=label, row_value=row_value, pdf_value=f.value,
                      result="equal" if ok else "differs", detail="" if ok else detail_if_differs)


def _compare(cr: CleanRow, pdf: _Pdf) -> tuple[Comparison, _Cand | None]:
    row = cr.row
    agree: list[str] = []
    checks: list[FieldCheck] = []

    ref = normalize_number(row.reference) if row.reference else None
    number_conflict = bool(ref and pdf.number and ref != pdf.number)
    if ref and pdf.number and ref == pdf.number:
        agree.append("number")
    if ref:
        checks.append(_check_field(pdf, "invoice_number", row.reference, "number" in agree))
    else:
        checks.append(FieldCheck(field="number", row_value="", pdf_value=_shown(pdf, "invoice_number"),
                                 result="not in row"))

    ratio = fuzz.token_sort_ratio(cr.vendor_key, pdf.vendor_key) if pdf.vendor_key else 0
    if pdf.vendor_key == cr.vendor_key:
        agree.append("vendor")
    vendor_similar = "vendor" not in agree and ratio >= VENDOR_SIMILAR
    vc = _check_field(pdf, "vendor", row.vendor, "vendor" in agree, f"similarity {ratio:.0f}")
    if vc.result == "equal":
        vc.detail = f"key {cr.vendor_key!r}"
    elif vendor_similar:
        vc.result, vc.detail = "similar", f"similarity {ratio:.0f}, keys {cr.vendor_key!r} / {pdf.vendor_key!r}"
    checks.append(vc)

    amount_diff = abs(row.amount - pdf.total) if pdf.total is not None else None
    if amount_diff is not None and amount_diff <= AMOUNT_TOL:
        agree.append("amount")
    delta = f"{row.amount - pdf.total:+} (row − PDF)" if pdf.total is not None else ""
    checks.append(_check_field(pdf, "total", str(row.amount), "amount" in agree, delta))

    date_diff = abs((row.invoice_date - pdf.invoice_date).days) if pdf.invoice_date else None
    if date_diff == 0:
        agree.append("date")
    days = f"{(row.invoice_date - pdf.invoice_date).days:+d} days (row − PDF)" if pdf.invoice_date else ""
    checks.append(_check_field(pdf, "invoice_date", row.invoice_date.isoformat(), "date" in agree, days))

    corroborated = "vendor" in agree and ("amount" in agree or "date" in agree)
    cand: _Cand | None = None
    rank_of = lambda eligible: (eligible, len(agree), ratio, -(amount_diff or 0), -(date_diff or 0))
    if "number" in agree:
        eligible = len(agree) >= 2
        note = "" if eligible else "only the invoice number agrees"
        cand = _Cand(pdf, agree, eligible, note, rank_of(eligible))
        outcome = ("can link: invoice number + " + ", ".join(agree[1:])) if eligible else f"review only: {note}"
    elif number_conflict and (corroborated or (vendor_similar and {"amount", "date"} <= set(agree))):
        note = f"invoice number differs: row {row.reference!r}, PDF {pdf.raw_number!r}"
        cand = _Cand(pdf, agree, False, note, rank_of(False))
        outcome = f"review only: {note}"
    elif number_conflict:
        outcome = "not a candidate: invoice number differs and too little else agrees"
    elif corroborated:
        cand = _Cand(pdf, agree, True, "", rank_of(True))
        outcome = "can link: " + " + ".join(agree)
    elif vendor_similar and {"amount", "date"} <= set(agree):
        note = f"vendor only similar: row {row.vendor!r}, PDF {pdf.vendor!r}"
        cand = _Cand(pdf, agree, False, note, rank_of(False))
        outcome = f"review only: {note}"
    else:
        outcome = f"not a candidate: agrees on {', '.join(agree) or 'nothing'}"
    return Comparison(invoice_file=pdf.file, checks=checks, outcome=outcome), cand


def _evidence(cr: CleanRow, c: _Cand) -> list[str]:
    row, pdf = cr.row, c.pdf
    text = {"number": f"invoice number {pdf.raw_number} equal",
            "vendor": f"vendor {pdf.vendor!r} equal (key {pdf.vendor_key!r})",
            "amount": f"amount {pdf.total} equal",
            "date": f"date {row.invoice_date.isoformat()} equal"}
    return [text[a] for a in c.agree]


def _snips(c: _Cand) -> list[Snip]:
    names = {"number": "invoice_number", "vendor": "vendor", "amount": "total", "date": "invoice_date"}
    return [c.pdf.snips[names[a]] for a in c.agree if names[a] in c.pdf.snips]


def _near_miss(comparisons: list[Comparison]) -> str:
    """The PDF that came closest, for a row with no candidate."""
    def agreeing(c: Comparison) -> list[FieldCheck]:
        return [k for k in c.checks if k.result in ("equal", "similar")]

    best = max(comparisons, key=lambda c: len(agreeing(c)), default=None)
    if best is None or not agreeing(best):
        return "Closest PDF: none agrees on any field"
    fields = ", ".join(f"{k.field} {k.result}" for k in agreeing(best))
    return f"Closest PDF: {best.invoice_file} ({fields}); {best.outcome}"


def _candidate(c: _Cand) -> Candidate:
    return Candidate(invoice_file=c.pdf.file, agree=c.agree, note=c.note)


def identify(rows: list[CleanRow], invoices: list[ExtractedInvoice]) -> list[Identification]:
    pdfs = [_trusted(inv) for inv in invoices]
    results: list[Identification] = []
    claims: dict[str, list[tuple[int, _Cand]]] = {}

    for cr in rows:
        row = cr.row
        compared = [_compare(cr, p) for p in pdfs]
        cands = sorted((c for _, c in compared if c), key=lambda c: c.rank, reverse=True)
        steps = [
            (f"Row: vendor {row.vendor!r} (key {cr.vendor_key!r}), amount {row.amount}, "
            f"date {row.invoice_date.isoformat()}, invoice no {row.reference or '—'}"),
            f"Compared with {len(pdfs)} PDFs: {len(cands)} candidate(s)"
            + "".join(f"\n  {c.pdf.file}: agrees on {', '.join(c.agree)}"
                      f" ({'can link' if c.eligible else 'review only: ' + c.note})" for c in cands),
        ]
        base = {"row_id": row.row_id, "excel_row": row.excel_row,
                "candidates": [_candidate(c) for c in cands], "comparisons": [cmp for cmp, _ in compared]}
        if not cands:
            steps.append(_near_miss([cmp for cmp, _ in compared]))
            steps.append("No PDF agrees on enough fields (rule 2/3) → no_evidence")
            results.append(Identification(**base, status="no_evidence", steps=steps,
                                          reason="no PDF agrees on two or more fields"))
            continue
        best = cands[0]
        if not best.eligible:
            steps.append(f"Best candidate {best.pdf.file} cannot link on its own: {best.note} → needs_review")
            results.append(Identification(**base, status="needs_review", reason=best.note, steps=steps))
            continue
        rivals = [c for c in cands[1:] if len(c.agree) >= len(best.agree)]
        if rivals:
            files = ", ".join(c.pdf.file for c in [best, *rivals])
            steps.append(f"Best {best.pdf.file} agrees on {len(best.agree)} fields, but "
                         f"{', '.join(c.pdf.file for c in rivals)} agree(s) on as many (rule 4) → needs_review")
            results.append(Identification(**base, status="needs_review", steps=steps,
                                          reason=f"ambiguous: {files} agree equally well"))
            continue
        runner_up = f"next best {cands[1].pdf.file} agrees on {len(cands[1].agree)}" if len(cands) > 1 \
            else "no other candidate"
        steps.append(f"Best {best.pdf.file} agrees on {len(best.agree)} fields; {runner_up} (rule 4: clear winner)")
        results.append(Identification(**base, status="linked", invoice_file=best.pdf.file, steps=steps,
                                      evidence=_evidence(cr, best), snips=_snips(best)))
        claims.setdefault(best.pdf.file, []).append((len(results) - 1, best))

    # One PDF backs one row: contested PDFs stay only with a strictly better row.
    for file, claim in claims.items():
        if len(claim) < 2:
            i = claim[0][0]
            results[i].steps.append(f"No other row claims {file} (rule 5) → linked")
            continue
        claim.sort(key=lambda ic: ic[1].rank, reverse=True)
        top_rank = claim[0][1].rank
        keep = claim[0][0] if claim[1][1].rank < top_rank else None
        others = ", ".join(results[i].row_id for i, _ in claim)
        for i, _ in claim:
            if i == keep:
                results[i].steps.append(f"{file} is also claimed by rows {others}; this row matches it "
                                        "strictly better (rule 5) → linked")
                continue
            results[i] = results[i].model_copy(update={
                "status": "needs_review", "invoice_file": None, "evidence": [], "snips": [],
                "reason": f"{file} is claimed by several rows ({others})",
                "steps": [*results[i].steps, (f"{file} is also claimed by rows {others} and this row is "
                          "not strictly better (rule 5) → needs_review")]})
    return results
