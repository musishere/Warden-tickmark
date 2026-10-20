"""A column's SHAPE without its contents: what a mapping proposal is allowed to see by default.

For each column: its header, how full and how unique it is, which field types its values parse
as, and a pattern — number formats, date formats, magnitudes in words, word counts, identifier
shapes (`INV-2026-1037` -> `AAA-9999-9999`). No cell value is ever copied into a profile; real
example values are a separate opt-in (TICKMARK_LLM_MAPPING_VALUES).
"""
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from tickmark.fields.mapping import _data_rows, column_values
from tickmark.fields.sheet import Sheet
from tickmark.fields.types import TYPES, clean_text
from tickmark.sample.cleaner import LEGAL_SUFFIXES

MAGNITUDES = ["below one", "units", "tens", "hundreds", "thousands", "tens of thousands",
              "hundreds of thousands", "millions", "tens of millions", "hundreds of millions", "billions"]


class ColumnProfile(BaseModel):
    col: str
    header: str | None
    filled: int  # non-empty values in data rows
    rows: int  # data rows
    distinct_share: float  # distinct values / filled, rounded to 0.1
    parses_as: list[str]  # field types at least 80% of the values parse as
    pattern: list[str]  # human-readable facts about the shape, no values
    examples: list[str] = Field(default_factory=list)  # only when real values are allowed


def _magnitude(x: Decimal) -> str:
    x = abs(x)
    if x < 1:
        return MAGNITUDES[0]
    return MAGNITUDES[min(len(str(int(x))), len(MAGNITUDES) - 1)]


def shape(text: str) -> str:
    """Letters -> A/a, digits -> 9, everything else kept: the form of an identifier, not its value."""
    return re.sub(r"[A-Z]", "A", re.sub(r"[a-z]", "a", re.sub(r"\d", "9", text)))


def _date_format(v) -> str:
    if isinstance(v, (datetime, date)):
        return "Excel date"
    t = clean_text(v)
    for rx, name in ((r"\d{4}-\d{2}-\d{2}", "yyyy-mm-dd"), (r"\d{1,2}\.\d{1,2}\.\d{4}", "dd.mm.yyyy"),
                     (r"\d{1,2}/\d{1,2}/\d{2,4}", "nn/nn/yyyy (day/month order unknown)"),
                     (r"\d{1,2}-\d{1,2}-\d{4}", "nn-nn-yyyy")):
        if re.fullmatch(rx, t):
            return name
    return "other text"


def _money_format(v) -> str:
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return "Excel number"
    t = clean_text(v)
    parts = []
    if re.search(r",\d{1,2}\D*$", t):
        parts.append("comma decimal")
    elif re.search(r"\.\d{1,2}\D*$", t):
        parts.append("dot decimal")
    if re.search(r"[€$£¥]|EUR|USD|GBP", t):
        parts.append("currency sign")
    if t.startswith(("(", "-")) or t.endswith("-"):
        parts.append("negative form")
    return "text: " + (", ".join(parts) or "digits only")


def _last_word(text: str) -> str:
    words = re.sub(r"[^\w]+", " ", text.lower().replace(".", "")).split()
    return words[-1] if words else ""


def _share(n: int, of: int) -> str:
    return f"{round(100 * n / of)}%" if of else "0%"


def profile_column(sheet: Sheet, col: str, with_examples: bool = False) -> ColumnProfile:
    header = next(h.header for h in sheet.headers if h.col == col)
    values = column_values(sheet, col)
    rows = sum(1 for _ in _data_rows(sheet))
    parses: dict[str, list] = {}
    for name in ("money", "date", "identifier"):
        ok = []
        for v in values:
            try:
                ok.append(TYPES[name].parse(v, name))
            except ValueError:
                pass
        parses[name] = ok
    parses_as = [n for n in ("money", "date") if values and len(parses[n]) / len(values) >= 0.8]
    texts = [clean_text(v) for v in values if isinstance(v, str)]
    if texts:
        parses_as.append("text")

    pattern: list[str] = []
    if not values:
        pattern.append("empty column")
    if "date" in parses_as:
        fmts = Counter(_date_format(v) for v in values)
        pattern.append("date formats: " + ", ".join(f"{f} ({_share(n, len(values))})" for f, n in fmts.most_common(3)))
        years = sorted({d.year for d in parses["date"]})
        pattern.append(f"years span {years[-1] - years[0] + 1}" if len(years) > 1 else "all in one year")
    elif "money" in parses_as:
        amounts = parses["money"]
        fmts = Counter(_money_format(v) for v in values)
        pattern.append("number formats: " + ", ".join(f"{f} ({_share(n, len(values))})" for f, n in fmts.most_common(3)))
        decimals = Counter(max(0, -a.as_tuple().exponent) for a in amounts)
        pattern.append("decimals: " + ", ".join(f"{d} ({_share(n, len(amounts))})" for d, n in sorted(decimals.items())))
        pattern.append(f"magnitudes from {_magnitude(min(amounts, key=abs))} to {_magnitude(max(amounts, key=abs))}")
        if any(a < 0 for a in amounts):
            pattern.append(f"negative values: {_share(sum(a < 0 for a in amounts), len(amounts))}")
    if texts and "date" not in parses_as and "money" not in parses_as:
        words = [len(t.split()) for t in texts]
        pattern.append(f"words per value: {min(words)}–{max(words)}")
        lengths = [len(t) for t in texts]
        pattern.append(f"characters per value: {min(lengths)}–{max(lengths)}")
        suffix = sum(1 for t in texts if _last_word(t) in LEGAL_SUFFIXES)
        if suffix:
            pattern.append(f"ends with a company legal form (B.V., GmbH, Ltd…): {_share(suffix, len(texts))}")
        if max(words) == 1 and max(lengths) <= 24:
            shapes = Counter(shape(t) for t in texts)
            pattern.append("shapes: " + ", ".join(f"{s} ({_share(n, len(texts))})" for s, n in shapes.most_common(3)))
    numbers_as_ids = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if numbers_as_ids and "money" not in parses_as and "date" not in parses_as:
        pattern.append(f"Excel numbers: {_share(len(numbers_as_ids), len(values))}")

    distinct = len({str(v) for v in values})
    return ColumnProfile(
        col=col, header=header, filled=len(values), rows=rows,
        distinct_share=round(distinct / len(values), 1) if values else 0.0,
        parses_as=parses_as, pattern=pattern,
        examples=[clean_text(v.isoformat() if isinstance(v, (date, datetime)) else v) for v in values[:5]]
        if with_examples else [])


def profile_sheet(sheet: Sheet, with_examples: bool = False) -> list[ColumnProfile]:
    return [profile_column(sheet, h.col, with_examples) for h in sheet.headers]
