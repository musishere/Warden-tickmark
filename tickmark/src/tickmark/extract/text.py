"""Find a row's value printed on a PDF without a label: a vendor in the letterhead, an invoice
number as a word. Works on the text lines a reader kept (tickmark.extract.local); empty otherwise."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from tickmark.models import ExtractedInvoice, Snip
from tickmark.sample.cleaner import normalize_vendor


def normalize_number(text: str) -> str:
    """'inv-2026 / 1037' and 'INV20261037' compare equal."""
    return re.sub(r"[\s\-/._#]", "", text).upper()


def found_in_text(inv: ExtractedInvoice, type_: str, role: str, value) -> Snip | None:
    """Where a row's value is printed on the PDF without a label, or None. Only two cases count:
    an identifier as one exact word (ignoring spaces, dashes and case), and the party's name as a whole
    block of the issuer's letterhead (never part of one: 'Contoso' is not 'Contoso Labs'; never
    elsewhere: there it may be the customer). Not found proves nothing."""
    if type_ == "identifier":
        want = normalize_number(str(value))
        for ln in inv.lines:
            if len(want) >= 3 and any(normalize_number(w) == want for w in ln.text.split()):
                return Snip(page=ln.page, bbox=ln.bbox, text=ln.text)
    elif type_ == "name" and role == "party":
        want = normalize_vendor(str(value))
        for ln in inv.lines[:inv.letterhead]:
            if want and normalize_vendor(ln.text) == want:
                return Snip(page=ln.page, bbox=ln.bbox, text=ln.text)
    return None


def untrusted(f) -> str:
    """Why a read value may not be used as evidence, or '' when it may: read with high confidence
    (settings.confidence_min), or confirmed by something independent (corroborate). Measured on real
    invoices, Azure's confidence alone did not tell right from wrong values."""
    from tickmark.config import settings

    if f.corroborated or (f.confidence is not None and f.confidence >= settings.confidence_min):
        return ""
    if f.confidence is None:
        return "no confidence reported"
    return f"read with confidence {f.confidence:.2f} and nothing confirms it"


def _date_forms(iso: str) -> set[str]:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return set()
    return {d.strftime(f) for f in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%y", "%d %B %Y",
                                    "%d %b %Y", "%B %d, %Y", "%Y%m%d")}


def corroborate(inv: ExtractedInvoice, tol: Decimal = Decimal("0.01")) -> None:
    """Mark values that independent evidence confirms: net + tax = total on the invoice; an invoice
    number, date, currency or vendor that is literally printed on the page (the text lines)."""
    def mark(name: str, why: str) -> None:
        if (f := inv.fields.get(name)) and not f.corroborated:
            inv.fields[name] = f.model_copy(update={"corroborated": why})

    try:
        sub, tax, total = (Decimal(inv.fields[n].value) for n in ("subtotal", "tax", "total"))
        if abs(sub + tax - total) <= tol:
            for n in ("subtotal", "tax", "total"):
                mark(n, "net + tax = total on the invoice")
    except (KeyError, InvalidOperation):
        pass
    if not inv.lines:
        return
    text = " " + " ".join(ln.text for ln in inv.lines) + " "
    words = {normalize_number(w) for ln in inv.lines for w in ln.text.split()}
    names = {normalize_vendor(ln.text) for ln in inv.lines}
    for name, f in list(inv.fields.items()):
        v = f.value.strip()
        if name in ("invoice_number", "po_number") and len(normalize_number(v)) >= 3 and normalize_number(v) in words or name in ("invoice_date", "due_date") and any(form in text for form in _date_forms(v)) or name == "currency" and (re.search(rf"\b{re.escape(v)}\b", text)
                                     or any(s in text for s, c in (("€", "EUR"), ("£", "GBP")) if c == v)) or name in ("vendor", "customer") and normalize_vendor(v) in names:
            mark(name, "printed on the invoice")
