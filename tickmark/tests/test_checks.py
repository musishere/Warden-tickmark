from datetime import date
from decimal import Decimal

from tickmark.checks.deterministic import date_in_period, is_duplicate, line_items_sum_to_total


def test_line_items_sum_to_total():
    assert line_items_sum_to_total([Decimal("10"), Decimal("5.50")], Decimal("15.50"))
    assert not line_items_sum_to_total([Decimal("10")], Decimal("15"))


def test_date_in_period():
    assert date_in_period(date(2025, 3, 1), date(2025, 1, 1), date(2025, 12, 31))


def test_duplicate_invoice_numbers():
    assert is_duplicate(" inv-1 ", {"INV-1"})
