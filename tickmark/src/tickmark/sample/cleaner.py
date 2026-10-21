"""Clean loaded sample rows without changing them: derive keys, flag issues, keep notes."""
import re
import unicodedata
from datetime import date

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.models import CleanedSample, CleaningNote, CleanRow, SampleRow

LEGAL_SUFFIXES = {
    "bv", "nv", "gmbh", "ag", "kg", "ltd", "limited", "plc", "inc", "incorporated",
    "llc", "sa", "srl", "co", "corp", "corporation",
    "sarl", "sas", "sasu", "eurl", "sci", "snc",  # France
    "ug", "ohg", "kgaa", "se", "ev", "gbr", "mbh",  # Germany, EU
    "spa", "sapa", "sl", "slu", "lda", "bvba", "vof", "cv", "ab", "as", "asa", "aps", "oy", "oyj",
    "kft", "sro", "spzoo", "doo", "pty", "lp", "llp", "pc",
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


def clean_sample(rows: list[SampleRow], period: tuple[date, date] | None = None,
                 definition: Definition = INVOICE_VOUCHING) -> CleanedSample:
    """Adds the party's matching key and flags, field by field from the definition: a duplicate
    (same party key + corroborating values as an earlier row), a row id used before, negative or
    zero money, a date outside the period."""
    cleaned: list[CleanRow] = []
    notes: list[CleaningNote] = []
    seen: dict[tuple, str] = {}
    ids: dict[str, int | None] = {}
    party = definition.by_role("party")[0]
    corroborating = definition.by_role("corroborating")
    what = ", ".join([party.label, *(s.label for s in corroborating[:-1])]) + f" and {corroborating[-1].label}"

    def flag(cr: CleanRow, rule: str, detail: str) -> None:
        cr.flags.append(rule)
        notes.append(CleaningNote(row_id=cr.row.row_id, rule=rule, detail=detail))

    for row in rows:
        name = row.get(party.name)
        cr = CleanRow(row=row, vendor_key=normalize_vendor(name) if name else "")
        key = (cr.vendor_key, *(row.get(s.name) for s in corroborating))
        if not name or all(v is None for v in key[1:]):
            pass  # without the party and a corroborating value, two rows cannot be called the same
        elif key in seen:
            flag(cr, f"duplicate_of:{seen[key]}", f"same {what} as row {seen[key]}")
        else:
            seen[key] = row.row_id
        if row.row_id in ids:
            flag(cr, f"duplicate_row_id:{row.row_id}", f"row id {row.row_id!r} also on Excel row {ids[row.row_id]}")
        else:
            ids[row.row_id] = row.excel_row
        for spec in definition.fields:
            v = row.get(spec.name)
            if spec.type == "money" and v is not None:
                if v < 0:
                    flag(cr, f"negative_{spec.label}", f"{v} (credit note?)")
                elif v == 0:
                    flag(cr, f"zero_{spec.label}", f"{spec.label} is 0")
            elif spec.type == "date" and v is not None and period and not (period[0] <= v <= period[1]):
                flag(cr, f"{spec.label}_outside_period", f"{v} not in {period[0]}..{period[1]}")
        cleaned.append(cr)
    return CleanedSample(rows=cleaned, notes=notes)
