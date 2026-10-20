"""Clean loaded sample rows without changing them: derive keys, flag issues, keep notes."""
import re
import unicodedata
from datetime import date
from decimal import Decimal

from tickmark.models import CleanedSample, CleaningNote, CleanRow, SampleRow

LEGAL_SUFFIXES = {
    "bv", "nv", "gmbh", "ag", "kg", "ltd", "limited", "plc", "inc", "incorporated",
    "llc", "sa", "srl", "co", "corp", "corporation",
}


def normalize_vendor(name: str) -> str:
    """Matching key for a vendor name. 'Acme B.V.', 'ACME BV' and 'acme  b.v' all become 'acme'."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ").replace(".", "")
    tokens = re.sub(r"[^\w]+", " ", text).split()
    full = " ".join(tokens)
    stripped = False
    # strip trailing legal suffixes, and an "and" left between them ("GmbH & Co. KG")
    while len(tokens) > 1 and (tokens[-1] in LEGAL_SUFFIXES or (tokens[-1] == "and" and stripped)):
        tokens.pop()
        stripped = True
    return " ".join(tokens) or full


def clean_sample(rows: list[SampleRow], period: tuple[date, date] | None = None) -> CleanedSample:
    cleaned: list[CleanRow] = []
    notes: list[CleaningNote] = []
    seen: dict[tuple[str, Decimal, date], str] = {}

    def flag(cr: CleanRow, rule: str, detail: str) -> None:
        cr.flags.append(rule)
        notes.append(CleaningNote(row_id=cr.row.row_id, rule=rule, detail=detail))

    for row in rows:
        cr = CleanRow(row=row, vendor_key=normalize_vendor(row.vendor))
        key = (cr.vendor_key, row.amount, row.invoice_date)
        if key in seen:
            flag(cr, f"duplicate_of:{seen[key]}", f"same vendor, amount and date as row {seen[key]}")
        else:
            seen[key] = row.row_id
        if row.amount < 0:
            flag(cr, "negative_amount", f"{row.amount} (credit note?)")
        elif row.amount == 0:
            flag(cr, "zero_amount", "amount is 0")
        if period and not (period[0] <= row.invoice_date <= period[1]):
            flag(cr, "date_outside_period", f"{row.invoice_date} not in {period[0]}..{period[1]}")
        cleaned.append(cr)
    return CleanedSample(rows=cleaned, notes=notes)
