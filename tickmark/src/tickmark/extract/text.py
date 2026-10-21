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
    elsewhere: there it may be the customer; never the customer the invoice names). Not found proves
    nothing."""
    if type_ == "identifier":
        want = normalize_number(str(value))
        for ln in inv.lines:
            if len(want) >= 3 and any(normalize_number(w) == want for w in ln.text.split()):
                return Snip(page=ln.page, bbox=ln.bbox, text=ln.text)
    elif type_ == "name" and role == "party":
        want = normalize_vendor(str(value))
        customer = inv.fields.get("customer")
        if not want or customer and normalize_vendor(customer.value) == want:  # the buyer is not the issuer
            return None
        for ln, text in _name_blocks(inv.lines[:inv.letterhead]):
            if normalize_vendor(text) == want:
                return Snip(page=ln.page, bbox=ln.bbox, text=text)
    return None


def _name_blocks(lines):
    """Each letterhead line whole, its parts between separators ('Name: X', 'X SARL - street - town'),
    and with the next line (a name broken over two lines). Still whole blocks, never part of one."""
    for i, ln in enumerate(lines):
        yield ln, ln.text
        if len(parts := re.split(r"\s[-–|•●]\s|:", ln.text)) > 1:
            yield from ((ln, p) for p in parts)
        if i + 1 < len(lines):
            yield ln, f"{ln.text} {lines[i + 1].text}"


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


def _iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _slash_order(text: str) -> str | None:
    """'dmy' or 'mdy' when the page's slash dates that read only one way (a part above 12) all agree."""
    orders = set()
    for a, b in re.findall(r"\b(\d{1,2})/(\d{1,2})/\d{2,4}\b", text):
        a, b = int(a), int(b)
        if a > 12 >= b:
            orders.add("dmy")
        elif b > 12 >= a:
            orders.add("mdy")
    return orders.pop() if len(orders) == 1 else None


def _printed(amount: Decimal, text: str) -> bool:
    """1234.5 as '1234.50', '1234,50', '1,234.50', '1.234,50' or '1 234,50' between non-digits."""
    plain = f"{amount:.2f}"
    grouped = f"{amount:,.2f}"
    forms = {plain, plain.replace(".", ","), grouped, grouped.translate(str.maketrans(",.", ".,")),
             grouped.replace(",", " ").replace(".", ",")}
    return any(re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d])", text) for f in forms)


def corroborate(inv: ExtractedInvoice, tol: Decimal = Decimal("0.01")) -> None:
    """Mark values that independent evidence confirms: net + tax = total on the invoice; an invoice
    number, date, currency or vendor that is literally printed on the page (the text lines)."""
    def mark(name: str, why: str) -> None:
        if (f := inv.fields.get(name)) and not f.corroborated:
            inv.fields[name] = f.model_copy(update={"corroborated": why})

    lines_text = " " + " ".join(ln.text for ln in inv.lines) + " " if inv.lines else ""
    try:  # r030: the tax was read in the booking currency (EUR 183.14); total - net (GBP 163.16) is printed too
        sub, total = Decimal(inv.fields["subtotal"].value), Decimal(inv.fields["total"].value)
        tax = Decimal(inv.fields["tax"].value) if "tax" in inv.fields else None
        if (tax is None or abs(sub + tax - total) > tol) and lines_text and _printed(total - sub, lines_text):
            was = inv.fields.get("tax")
            inv.fields["tax"] = (was or inv.fields["subtotal"]).model_copy(update={
                "name": "tax", "value": f"{total - sub:f}",
                "corroborated": f"total - net, printed on the invoice{f' (read {was.value})' if was else ''}"})
    except (KeyError, InvalidOperation):
        pass
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
    if _slash_order(text) == "mdy":  # a reader assumed day first; this page writes month first
        for name in ("invoice_date", "due_date"):
            if (f := inv.fields.get(name)) and (d := _iso(f.value)) and d.day <= 12 and d.day != d.month \
                    and d.strftime("%d/%m/%Y") in text:
                inv.fields[name] = f.model_copy(update={
                    "value": date(d.year, d.day, d.month).isoformat(),
                    "corroborated": f"printed {d.strftime('%d/%m/%Y')}: month first, as other dates on the page"})
    words = {normalize_number(w) for ln in inv.lines for w in ln.text.split()}
    names = {normalize_vendor(ln.text) for ln in inv.lines}
    for name, f in list(inv.fields.items()):
        v = f.value.strip()
        if name in ("invoice_number", "po_number") and len(normalize_number(v)) >= 3 and normalize_number(v) in words or name in ("invoice_date", "due_date") and any(form in text for form in _date_forms(v)) or name == "currency" and (re.search(rf"\b{re.escape(v)}\b", text)
                                     or any(s in text for s, c in (("€", "EUR"), ("£", "GBP")) if c == v)) or name in ("vendor", "customer") and normalize_vendor(v) in names:
            mark(name, "printed on the invoice")
