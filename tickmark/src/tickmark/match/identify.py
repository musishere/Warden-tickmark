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

from tickmark.definitions import INVOICE_VOUCHING, Definition, FieldSpec
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
    shown: dict[str, str] = field(default_factory=dict)  # evidence field -> trusted value as read
    value: dict[str, object] = field(default_factory=dict)  # evidence field -> typed trusted value
    key: dict[str, str] = field(default_factory=dict)  # evidence field -> matching key (names, numbers)
    snips: dict[str, Snip] = field(default_factory=dict)
    fields: dict[str, PdfField] = field(default_factory=dict)


@dataclass
class _Cand:
    pdf: _Pdf
    agree: list[str]
    eligible: bool
    note: str
    rank: tuple


def _noun(spec: FieldSpec) -> str:
    """'the invoice number' -> 'invoice number': how a field is named in notes."""
    return spec.description.removeprefix("the ") or spec.label


def pdf_fields(inv: ExtractedInvoice, definition: Definition = INVOICE_VOUCHING) -> list[PdfField]:
    """What was read from a PDF and whether each value may be used as evidence (and why not)."""
    return list(_trusted(inv, definition).fields.values())


def _trusted(inv: ExtractedInvoice, definition: Definition = INVOICE_VOUCHING) -> _Pdf:
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

    for spec in definition.compared:
        ev = spec.evidence_field
        v = value(ev)
        if v is None:
            continue
        if spec.type == "identifier":
            if k := normalize_number(v):
                pdf.shown[ev], pdf.key[ev] = v, k
        elif spec.type == "name":
            pdf.shown[ev], pdf.key[ev] = v, normalize_vendor(v)
        elif spec.type == "date":
            try:
                pdf.value[ev], pdf.shown[ev] = date.fromisoformat(v), v
            except ValueError:
                distrust(ev, "not a date")
        elif spec.type == "money" and ev not in definition.reconcile and (m := money(ev)) is not None:
            pdf.value[ev], pdf.shown[ev] = m, v
    # a total counts only if it reconciles with its parts (total = subtotal + tax)
    for ev, parts in definition.reconcile.items():
        total, part_values = money(ev), [money(p) for p in parts]  # parts parsed even without a total
        if total is None:
            continue
        if any(pv is None for pv in part_values):
            distrust(ev, f"cannot reconcile: {' or '.join(parts)} not read")
        elif abs(sum(part_values) - total) > AMOUNT_TOL:
            terms = " + ".join(f"{p} {pv}" for p, pv in zip(parts, part_values, strict=True))
            distrust(ev, f"{terms} = {sum(part_values)} ≠ {ev} {total}")
        else:
            pdf.value[ev], pdf.shown[ev] = total, str(total)
    return pdf


def _shown(pdf: _Pdf, name: str) -> str:
    f = pdf.fields.get(name)
    return f.value if f else ""


def _check_field(pdf: _Pdf, spec: FieldSpec, row_value: str, ok: bool, detail_if_differs: str = "") -> FieldCheck:
    f = pdf.fields.get(spec.evidence_field)
    if f is None:
        return FieldCheck(field=spec.label, row_value=row_value, pdf_value="", result="missing")
    if not f.trusted:
        return FieldCheck(field=spec.label, row_value=row_value, pdf_value=f.value, result="untrusted",
                          detail=f.why_not)
    return FieldCheck(field=spec.label, row_value=row_value, pdf_value=f.value,
                      result="equal" if ok else "differs", detail="" if ok else detail_if_differs)


def _compare(cr: CleanRow, pdf: _Pdf, definition: Definition = INVOICE_VOUCHING) -> tuple[Comparison, _Cand | None]:
    """This row vs one PDF, field by field in the definition's order, then the linking rules
    (module docstring) applied by role: key, party, corroborating."""
    row = cr.row
    agree: list[str] = []
    checks: list[FieldCheck] = []
    key_spec = next(iter(definition.by_role("key")), None)
    party = definition.by_role("party")[0]
    corroborating = [s.label for s in definition.by_role("corroborating")]
    key_conflict = party_similar = False
    ratio, money_diff, date_diff = 0.0, Decimal(0), 0

    for spec in definition.compared:
        ev, rv = spec.evidence_field, row.get(spec.name)
        if rv is None:
            checks.append(FieldCheck(field=spec.label, row_value="", pdf_value=_shown(pdf, ev), result="not in row"))
            continue
        if spec.role == "key":
            ref = normalize_number(rv)
            if ev in pdf.key and ref == pdf.key[ev]:
                agree.append(spec.label)
            key_conflict = bool(ref and ev in pdf.key and ref != pdf.key[ev])
            checks.append(_check_field(pdf, spec, rv, spec.label in agree))
        elif spec.type == "name":
            ratio = fuzz.token_sort_ratio(cr.vendor_key, pdf.key[ev]) if ev in pdf.key else 0
            if pdf.key.get(ev) == cr.vendor_key:
                agree.append(spec.label)
            party_similar = spec.label not in agree and ratio >= VENDOR_SIMILAR
            fc = _check_field(pdf, spec, rv, spec.label in agree, f"similarity {ratio:.0f}")
            if fc.result == "equal":
                fc.detail = f"key {cr.vendor_key!r}"
            elif party_similar:
                fc.result, fc.detail = "similar", f"similarity {ratio:.0f}, keys {cr.vendor_key!r} / {pdf.key[ev]!r}"
            checks.append(fc)
        elif spec.type == "money":
            pv = pdf.value.get(ev)
            diff = abs(rv - pv) if pv is not None else None
            if diff is not None and diff <= AMOUNT_TOL:
                agree.append(spec.label)
            money_diff += diff or 0
            checks.append(_check_field(pdf, spec, str(rv), spec.label in agree,
                                       f"{rv - pv:+} (row − PDF)" if pv is not None else ""))
        elif spec.type == "date":
            pv = pdf.value.get(ev)
            days = (rv - pv).days if pv is not None else None
            if days == 0:
                agree.append(spec.label)
            date_diff += abs(days or 0)
            checks.append(_check_field(pdf, spec, rv.isoformat(), spec.label in agree,
                                       f"{days:+d} days (row − PDF)" if pv is not None else ""))

    key = key_spec.label if key_spec else None
    corroborated = party.label in agree and any(c in agree for c in corroborating)
    all_corroborating = set(corroborating) <= set(agree)
    cand: _Cand | None = None
    rank_of = lambda eligible: (eligible, len(agree), ratio, -money_diff, -date_diff)
    if key and key in agree:
        eligible = len(agree) >= 2
        note = "" if eligible else f"only the {_noun(key_spec)} agrees"
        cand = _Cand(pdf, agree, eligible, note, rank_of(eligible))
        outcome = (f"can link: {_noun(key_spec)} + " + ", ".join(a for a in agree if a != key)) if eligible \
            else f"review only: {note}"
    elif key_conflict and (corroborated or (party_similar and all_corroborating)):
        note = (f"{_noun(key_spec)} differs: row {row.get(key_spec.name)!r}, "
                f"PDF {pdf.shown.get(key_spec.evidence_field)!r}")
        cand = _Cand(pdf, agree, False, note, rank_of(False))
        outcome = f"review only: {note}"
    elif key_conflict:
        outcome = f"not a candidate: {_noun(key_spec)} differs and too little else agrees"
    elif corroborated:
        cand = _Cand(pdf, agree, True, "", rank_of(True))
        outcome = "can link: " + " + ".join(agree)
    elif party_similar and all_corroborating:
        note = (f"{party.label} only similar: row {row.get(party.name)!r}, "
                f"PDF {pdf.shown.get(party.evidence_field)!r}")
        cand = _Cand(pdf, agree, False, note, rank_of(False))
        outcome = f"review only: {note}"
    else:
        outcome = f"not a candidate: agrees on {', '.join(agree) or 'nothing'}"
    return Comparison(invoice_file=pdf.file, checks=checks, outcome=outcome), cand


def _evidence(cr: CleanRow, c: _Cand, definition: Definition = INVOICE_VOUCHING) -> list[str]:
    by_label = {s.label: s for s in definition.compared}
    out = []
    for a in c.agree:
        spec = by_label[a]
        shown = c.pdf.shown.get(spec.evidence_field)
        if spec.role == "key":
            out.append(f"{_noun(spec)} {shown} equal")
        elif spec.type == "name":
            out.append(f"{spec.label} {shown!r} equal (key {c.pdf.key[spec.evidence_field]!r})")
        else:
            out.append(f"{spec.label} {shown} equal")
    return out


def _snips(c: _Cand, definition: Definition = INVOICE_VOUCHING) -> list[Snip]:
    names = {s.label: s.evidence_field for s in definition.compared}
    return [c.pdf.snips[names[a]] for a in c.agree if names[a] in c.pdf.snips]


def _describe(cr: CleanRow, spec: FieldSpec) -> str:
    v = cr.row.get(spec.name)
    if spec.type == "name":
        return f"{spec.label} {v!r} (key {cr.vendor_key!r})"
    return f"{spec.label} {v.isoformat() if isinstance(v, date) else v}"


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


def identify(rows: list[CleanRow], invoices: list[ExtractedInvoice],
             definition: Definition = INVOICE_VOUCHING) -> list[Identification]:
    pdfs = [_trusted(inv, definition) for inv in invoices]
    results: list[Identification] = []
    claims: dict[str, list[tuple[int, _Cand]]] = {}

    for cr in rows:
        row = cr.row
        compared = [_compare(cr, p, definition) for p in pdfs]
        cands = sorted((c for _, c in compared if c), key=lambda c: c.rank, reverse=True)
        steps = [
            "Row: " + ", ".join(_describe(cr, spec) for spec in definition.compared if spec.role != "key")
            + "".join(f", {_noun(spec)} {row.get(spec.name) or '—'}" for spec in definition.by_role("key")),
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
                                      evidence=_evidence(cr, best, definition), snips=_snips(best, definition)))
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
