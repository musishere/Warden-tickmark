"""The fixed set of field types. Which fields an audit test uses is decided at runtime (a
definition); how a value of each type is parsed and stored is fixed here, tested once.

Every type turns a raw Excel cell into a Python value (or raises ValueError with a reason the
auditor can read), and to and from a canonical string for JSONB storage. Money is always a
string there, never a JSON number, so no float ever touches an amount.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b‌‍﻿"))


def clean_text(value) -> str:
    """Remove zero-width characters, turn NBSP/tabs into spaces, collapse whitespace."""
    text = str(value or "").translate(ZERO_WIDTH).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_amount(value, field: str = "amount") -> Decimal:
    if value is None or isinstance(value, bool) or value == "":
        raise ValueError(f"{field} is missing or invalid")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    raw = clean_text(value)
    if re.search(r"[^\d\s.,()+\-€$£¥]", re.sub(r"\b(?:EUR|USD|GBP|CHF)\b", "", raw, flags=re.IGNORECASE)):
        raise ValueError(f"{field} is not a number: {value!r}")  # letters: an id or a note, never an amount
    negative = False
    if raw.startswith("(") and raw.endswith(")"):  # accounting negative: (1,234.50)
        negative, raw = True, raw[1:-1]
    elif raw.endswith("-"):  # trailing minus: 123.45-
        negative, raw = True, raw[:-1]
    text = re.sub(r"[^\d,.\-]", "", raw)
    if "," in text and "." in text:  # last separator is the decimal one
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else text.replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{field} is not a number: {value!r}") from None
    return -amount if negative else amount


def parse_date(value, field: str = "date") -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text):  # dd.mm.yyyy
            try:
                return date(int(m[3]), int(m[2]), int(m[1]))
            except ValueError:
                pass
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text):
            raise ValueError(f"ambiguous date (day/month order unknown): {value!r}")
    raise ValueError(f"{field} is missing or not YYYY-MM-DD / DD.MM.YYYY: {value!r}")


def parse_text(value, field: str = "text") -> str:
    text = clean_text(value.isoformat() if isinstance(value, (date, datetime)) else value)
    if not text:
        raise ValueError(f"{field} is empty")
    return text


def parse_identifier(value, field: str = "identifier") -> str:
    """As written; 7.0 from Excel becomes '7'."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return parse_text(value, field)


@dataclass(frozen=True)
class FieldType:
    name: str
    parse: Callable[[Any, str], Any]  # raw cell -> Python value, or ValueError(reason)
    to_str: Callable[[Any], str]  # Python value -> canonical string (JSONB)
    from_str: Callable[[str], Any]  # canonical string -> Python value


TYPES: dict[str, FieldType] = {
    "money": FieldType("money", parse_amount, lambda v: f"{v:f}", Decimal),
    "date": FieldType("date", parse_date, date.isoformat, date.fromisoformat),
    "name": FieldType("name", parse_text, str, str),
    "identifier": FieldType("identifier", parse_identifier, str, str),
    "text": FieldType("text", parse_text, str, str),
}
