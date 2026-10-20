"""Row vs its linked PDF, field by field: does the PDF say what the row claims?"""
from datetime import date
from decimal import Decimal, InvalidOperation

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


def compare(cr: CleanRow, inv: ExtractedInvoice, amount_tol: Decimal = Decimal("0.01")) -> list[FieldAgreement]:
    row = cr.row
    out: list[FieldAgreement] = []

    def unverifiable(field: str, row_value: str, name: str, why: str) -> FieldAgreement:
        return FieldAgreement(field=field, row_value=row_value, pdf_value=_shown(inv, name),
                              result="unverifiable", detail=why)

    if row.reference:
        number, why = read(inv, "invoice_number")
        if number is None:
            out.append(unverifiable("number", row.reference, "invoice_number", why))
        else:
            same = normalize_number(number) == normalize_number(row.reference)
            out.append(FieldAgreement(field="number", row_value=row.reference, pdf_value=number,
                                      result="agrees" if same else "differs"))

    vendor, why = read(inv, "vendor")
    if vendor is None:
        out.append(unverifiable("vendor", row.vendor, "vendor", why))
    else:
        key = normalize_vendor(vendor)
        same = key == cr.vendor_key
        out.append(FieldAgreement(field="vendor", row_value=row.vendor, pdf_value=vendor,
                                  result="agrees" if same else "differs",
                                  detail=f"key {key!r}" if same else f"keys {cr.vendor_key!r} / {key!r}"))

    total, why = read_amount(inv, "total")  # the printed total: what the invoice claims
    if total is None:
        out.append(unverifiable("amount", str(row.amount), "total", why))
    else:
        diff = row.amount - total
        out.append(FieldAgreement(field="amount", row_value=str(row.amount), pdf_value=str(total),
                                  result="agrees" if abs(diff) <= amount_tol else "differs",
                                  delta="" if abs(diff) <= amount_tol else f"{diff:+} (row − PDF)"))

    d, why = read_date(inv, "invoice_date")
    if d is None:
        out.append(unverifiable("date", row.invoice_date.isoformat(), "invoice_date", why))
    else:
        days = (row.invoice_date - d).days
        out.append(FieldAgreement(field="date", row_value=row.invoice_date.isoformat(), pdf_value=d.isoformat(),
                                  result="agrees" if days == 0 else "differs",
                                  delta="" if days == 0 else f"{days:+d} days (row − PDF)"))
    return out
