"""Find a row's value printed on a PDF without a label: a vendor in the letterhead, an invoice
number as a word. Works on the text lines a reader kept (tickmark.extract.local); empty otherwise."""
import re

from tickmark.models import ExtractedInvoice, Snip
from tickmark.sample.cleaner import normalize_vendor


def normalize_number(text: str) -> str:
    """'inv-2026 / 1037' and 'INV20261037' compare equal."""
    return re.sub(r"[\s\-/._#]", "", text).upper()


def found_in_text(inv: ExtractedInvoice, type_: str, role: str, value) -> Snip | None:
    """Where a row's value is printed on the PDF without a label, or None. Only two cases count:
    an identifier as one exact word (ignoring spaces, dashes and case), and the party's name in the
    issuer's letterhead (never elsewhere: there it may be the customer). Not found proves nothing."""
    if type_ == "identifier":
        want = normalize_number(str(value))
        for ln in inv.lines:
            if len(want) >= 3 and any(normalize_number(w) == want for w in ln.text.split()):
                return Snip(page=ln.page, bbox=ln.bbox, text=ln.text)
    elif type_ == "name" and role == "party":
        want = normalize_vendor(str(value)).split()
        for ln in inv.lines[:inv.letterhead]:
            words = normalize_vendor(ln.text).split() + [""]
            if want and any(words[i:i + len(want)] == want for i in range(len(words))):
                return Snip(page=ln.page, bbox=ln.bbox, text=ln.text)
    return None
