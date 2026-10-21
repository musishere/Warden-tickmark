"""LLM proposes, code decides: pass/fail is always computed here."""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def line_items_sum_to_total(items: list[Decimal], total: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    return abs(sum(items, Decimal(0)) - total) <= tol


def vat_is_correct(net: Decimal, vat: Decimal, rate: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    return abs(net * rate - vat) <= tol


def vat_rate_matches(net: Decimal, vat: Decimal, rates: list[Decimal], tol: Decimal = Decimal("0.01")) -> Decimal | None:
    """The allowed rate the tax was computed with (rounded to the cent), or None."""
    return next((r for r in rates if abs(money(net * r) - vat) <= tol), None)


def line_arithmetic(quantity: Decimal, unit_price: Decimal, amount: Decimal, tol: Decimal = Decimal("0.01")) -> bool:
    return abs(money(quantity * unit_price) - amount) <= tol


def date_in_period(d: date, start: date, end: date) -> bool:
    return start <= d <= end


def is_duplicate(invoice_number: str, seen: set[str]) -> bool:
    return invoice_number.strip().upper() in seen


def duplicates(invoices: list[tuple[str, str, str, str]]) -> dict[str, list[tuple[str, str]]]:
    """invoices: (file, vendor_key, normalised number, number as printed). For each file that shares its number with
    another file: ("fail", ...) if the same vendor (a possible double payment), ("warn", ...) if a
    different vendor (unusual, but numbers are only unique per vendor), ("unclear", ...) if a vendor
    was not read ("")."""
    found: dict[str, list[tuple[str, str]]] = {}
    for file, vendor, number, printed in invoices:
        for other, other_vendor, other_number, _ in invoices:
            if other == file or other_number != number:
                continue
            if not vendor or not other_vendor:
                found.setdefault(file, []).append(("unclear", (f"number {printed} also used by {other}; "
                                                               "vendor not read, so same or other supplier is unknown")))
            elif other_vendor == vendor:
                found.setdefault(file, []).append(("fail", f"same vendor and number {printed} as {other}"))
            else:
                found.setdefault(file, []).append(("warn", f"number {printed} also used by {other} ({other_vendor})"))
    return found
