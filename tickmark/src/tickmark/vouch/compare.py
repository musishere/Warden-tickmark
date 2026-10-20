"""Row vs its linked PDF, field by field: does the PDF say what the row claims?"""
from datetime import date
from decimal import Decimal, InvalidOperation

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.match.identify import CONFIDENCE_MIN, normalize_number
from tickmark.models import CleanRow, ExtractedInvoice, FieldAgreement
from tickmark.sample import normalize_vendor


def read(inv: ExtractedInvoice, name: str) -> tuple[str | None, str]:
    """A value usable as evidence, or (None, why not)."""
    f = inv.get(name)
    if f is None or not f.value.strip():
        return None, "not read from the PDF"
    if f.confidence is None or f.confidence < CONFIDENCE_MIN:
        return None, f"read with low confidence ({f.confidence})"
    return f.value.strip(), ""


def read_amount(inv: ExtractedInvoice, name: str) -> tuple[Decimal | None, str]:
    v, why = read(inv, name)
    try:
        return (Decimal(v), "") if v is not None else (None, why)
    except InvalidOperation:
        return None, f"not a number: {v!r}"


def read_date(inv: ExtractedInvoice, name: str) -> tuple[date | None, str]:
    v, why = read(inv, name)
    try:
        return (date.fromisoformat(v), "") if v is not None else (None, why)
    except ValueError:
        return None, f"not a date: {v!r}"


def _shown(inv: ExtractedInvoice, name: str) -> str:
    f = inv.get(name)
    return f.value if f else ""


def compare(cr: CleanRow, inv: ExtractedInvoice, amount_tol: Decimal = Decimal("0.01"),
            definition: Definition = INVOICE_VOUCHING) -> list[FieldAgreement]:
    """Each claimed field the definition compares, in its order, by type. An optional field the
    row leaves empty is not compared."""
    row = cr.row
    out: list[FieldAgreement] = []
    for spec in definition.compared:
        rv, ev, label = row.get(spec.name), spec.evidence_field, spec.label
        if rv is None:
            continue
        shown = rv.isoformat() if isinstance(rv, date) else str(rv)
        if spec.type == "money":
            pv, why = read_amount(inv, ev)  # for an invoice: the printed total, what the invoice claims
        elif spec.type == "date":
            pv, why = read_date(inv, ev)
        else:
            pv, why = read(inv, ev)
        if pv is None:
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=_shown(inv, ev),
                                      result="unverifiable", detail=why))
        elif spec.type == "money":
            diff = rv - pv
            ok = abs(diff) <= amount_tol
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=str(pv), result="agrees" if ok else "differs",
                                      delta="" if ok else f"{diff:+} (row − PDF)"))
        elif spec.type == "date":
            days = (rv - pv).days
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv.isoformat(),
                                      result="agrees" if days == 0 else "differs",
                                      delta="" if days == 0 else f"{days:+d} days (row − PDF)"))
        elif spec.type == "name":
            key = normalize_vendor(pv)
            same = key == cr.vendor_key
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv, result="agrees" if same else "differs",
                                      detail=f"key {key!r}" if same else f"keys {cr.vendor_key!r} / {key!r}"))
        else:  # identifier, text
            same = normalize_number(pv) == normalize_number(rv) if spec.type == "identifier" else pv == rv
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv, result="agrees" if same else "differs"))
    return out
