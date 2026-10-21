"""Row vs its linked PDF, field by field: does the PDF say what the row claims?"""
from datetime import date
from decimal import Decimal, InvalidOperation

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.extract.text import found_in_text, untrusted
from tickmark.match.identify import normalize_number
from tickmark.models import CleanRow, ExtractedInvoice, FieldAgreement
from tickmark.sample import normalize_vendor


def read(inv: ExtractedInvoice, name: str) -> tuple[str | None, str]:
    """A value usable as evidence, or (None, why not)."""
    f = inv.get(name)
    if f is None or not f.value.strip():
        return None, f"not read from the PDF ({inv.unread[name]})" if name in inv.unread else "not read from the PDF"
    if why := untrusted(f):
        return None, why
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


def _weak_equal(type_: str, rv, f, tol: Decimal) -> bool:
    """A value read but not trusted (low confidence) that is exactly the row's claim."""
    if f is None or not f.value.strip():
        return False
    v = f.value.strip()
    try:
        if type_ == "money":
            return abs(rv - Decimal(v)) <= tol
        if type_ == "date":
            return rv == date.fromisoformat(v)
    except (InvalidOperation, ValueError):
        return False
    if type_ == "name":
        return normalize_vendor(v) == normalize_vendor(str(rv))
    if type_ == "identifier":
        return normalize_number(v) == normalize_number(str(rv))
    return v.upper() == str(rv).upper() if type_ == "currency" else v == str(rv)


def _day_month_swapped(a: date, b: date) -> bool:
    return a.year == b.year and a.day == b.month and a.month == b.day and a.day <= 12 and a.month <= 12


def _part_of(a: str, b: str) -> bool:
    """One name's words appear, in order, inside the other's: a partly read or a look-alike name."""
    short, long_ = sorted((a.split(), b.split()), key=len)
    return bool(short) and any(long_[i:i + len(short)] == short for i in range(len(long_) - len(short) + 1))


def _shown(inv: ExtractedInvoice, name: str) -> str:
    f = inv.get(name)
    return f.value if f else ""


def compare(cr: CleanRow, inv: ExtractedInvoice, amount_tol: Decimal = Decimal("0.01"),
            definition: Definition = INVOICE_VOUCHING, defaults: dict | None = None) -> list[FieldAgreement]:
    """Each claimed field the definition compares, in its order, by type. An optional field the
    row leaves empty is not compared, unless the definition says it must be (compare_when_empty):
    then the engagement's value is used (defaults, e.g. its currency), and without one it is
    unverifiable."""
    row = cr.row
    out: list[FieldAgreement] = []
    for spec in definition.compared:
        rv, ev, label = row.get(spec.name), spec.evidence_field, spec.label
        source = ""
        if rv is None and spec.compare_when_empty:
            rv, source = (defaults or {}).get(spec.name), " (the engagement's)"
            if rv is None:
                out.append(FieldAgreement(field=label, row_value="", pdf_value=_shown(inv, ev), result="unverifiable",
                                          detail=f"the row has no {label} and the engagement sets none"))
                continue
        if rv is None:
            continue
        shown = rv.isoformat() if isinstance(rv, date) else str(rv)
        if spec.type == "money":
            pv, why = read_amount(inv, ev)  # for an invoice: the printed total, what the invoice claims
        elif spec.type == "date":
            pv, why = read_date(inv, ev)
        else:
            pv, why = read(inv, ev)
        if pv is None and (snip := found_in_text(inv, spec.type, spec.role, rv)):
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=snip.text, result="agrees",
                                      detail=f"printed without a label on page {snip.page}"))
        elif pv is None and _weak_equal(spec.type, rv, inv.get(ev), amount_tol):
            # read with low confidence, but exactly the claim: the reader never saw the claim, so that agrees
            out.append(FieldAgreement(field=label, row_value=shown + source, pdf_value=inv.get(ev).value,
                                      result="agrees", detail=f"{why}; equals the row"))
        elif pv is None:
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=_shown(inv, ev),
                                      result="unverifiable", detail=why))
        elif spec.type == "money":
            diff = rv - pv
            ok = abs(diff) <= amount_tol
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=str(pv), result="agrees" if ok else "differs",
                                      delta="" if ok else f"{diff:+} (row − PDF)"))
        elif spec.type == "date":
            days = (rv - pv).days
            if days and _day_month_swapped(rv, pv):  # 03/11 is 3 November or 11 March: the reader may have guessed
                out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv.isoformat(), result="unverifiable",
                                          detail="day and month swapped: the printed date reads both ways"))
                continue
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv.isoformat(),
                                      result="agrees" if days == 0 else "differs",
                                      delta="" if days == 0 else f"{days:+d} days (row − PDF)"))
        elif spec.type == "name":
            key, row_key = normalize_vendor(pv), normalize_vendor(rv)
            same = key == row_key
            if not same and _part_of(key, row_key):  # 'Versicherungsverein Musterstadt' of 'MVM Musterhafter ...'
                out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv, result="unverifiable",
                                          detail=f"one name is part of the other: keys {row_key!r} / {key!r}"))
                continue
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv, result="agrees" if same else "differs",
                                      detail=f"key {key!r}" if same else f"keys {row_key!r} / {key!r}"))
        elif spec.type == "currency":
            same = pv.upper() == rv
            out.append(FieldAgreement(field=label, row_value=shown + source, pdf_value=pv,
                                      result="agrees" if same else "differs"))
        else:  # identifier, text
            same = normalize_number(pv) == normalize_number(rv) if spec.type == "identifier" else pv == rv
            out.append(FieldAgreement(field=label, row_value=shown, pdf_value=pv, result="agrees" if same else "differs"))
    return out
