from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from tickmark.config import settings


def parse_rates(text: str) -> list[Decimal]:
    """'0, 7, 19' (percent) -> [0, 0.07, 0.19]."""
    try:
        return [Decimal(x.strip().rstrip("%")) / 100 for x in text.split(",") if x.strip()]
    except ArithmeticError:
        raise ValueError(f"VAT rates must be numbers in %, comma separated: {text!r}") from None


class VouchPolicy(BaseModel):
    """The engagement's settings a verdict depends on. Recorded with every stored run."""

    period_start: date | None = None  # the audit period; without it in_period is not checkable
    period_end: date | None = None
    vat_rates: list[Decimal] = Field(default_factory=lambda: parse_rates(settings.vat_rates))
    amount_tol: Decimal = Decimal("0.01")  # any difference above this is ⚠️ (the auditor judges materiality)
    currency: str | None = None  # the engagement's currency, for rows that do not state one
    line_items: bool = False  # check that item lines add up (reading the table needs Azure); off unless asked


def parse_period(text: str) -> tuple[date, date]:
    """'2026-01-01:2026-12-31' -> (start, end)."""
    start, _, end = text.partition(":")
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b < a:
        raise ValueError(f"period ends before it starts: {text}")
    return a, b
