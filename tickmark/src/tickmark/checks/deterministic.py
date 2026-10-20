"""LLM proposes, code decides: pass/fail is always computed here."""
from datetime import date
from decimal import Decimal


def line_items_sum_to_total(items: list[Decimal], total: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    return abs(sum(items, Decimal(0)) - total) <= tol


def vat_is_correct(net: Decimal, vat: Decimal, rate: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    return abs(net * rate - vat) <= tol


def date_in_period(d: date, start: date, end: date) -> bool:
    return start <= d <= end


def is_duplicate(invoice_number: str, seen: set[str]) -> bool:
    return invoice_number.strip().upper() in seen
