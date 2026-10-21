import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from tickmark.sample import SampleFormatError, load_sample


def make(tmp_path: Path, rows: list[list]) -> Path:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    p = tmp_path / "s.xlsx"
    wb.save(p)
    return p


def test_happy_path(tmp_path):
    p = make(tmp_path, [["Row", "Vendor", "Amount", "Date"], [1, " Acme ", 10.5, date(2025, 3, 1)]])
    res = load_sample(p)
    assert res.errors == []
    row = res.rows[0]
    assert (row.row_id, row.vendor, row.amount, row.invoice_date) == ("1", "Acme", Decimal("10.5"), date(2025, 3, 1))


def test_aliases_and_column_order(tmp_path):
    p = make(tmp_path, [["Invoice Date", "Supplier", "Total"], ["2025-03-01", "Acme", "1,234.50"]])
    row = load_sample(p).rows[0]
    assert row.amount == Decimal("1234.50") and row.invoice_date == date(2025, 3, 1)
    assert row.row_id == "2"  # falls back to the Excel row number


def test_european_amount(tmp_path):
    p = make(tmp_path, [["Vendor", "Amount", "Date"], ["A", "1.234,50", "2025-01-02"]])
    assert load_sample(p).rows[0].amount == Decimal("1234.50")


def test_a_sheet_must_be_able_to_identify_an_invoice(tmp_path):
    with pytest.raises(SampleFormatError, match="cannot identify an invoice"):
        load_sample(make(tmp_path, [["Vendor", "Notes"], ["A", "x"]]))
    assert load_sample(make(tmp_path, [["Vendor", "Date"], ["A", "2025-01-01"]])).rows[0].amount is None
    assert load_sample(make(tmp_path, [["Invoice No", "Amount"], ["X-1", 10]])).rows[0].reference == "X-1"


def test_bad_rows_reported_and_others_load(tmp_path):
    p = make(tmp_path, [
        ["Vendor", "Amount", "Date"],
        ["A", "abc", "2025-01-01"],
        ["", 5, "2025-01-01"],
        ["B", 5, "not a date"],
        ["C", 7, "2025-01-01"],
    ])
    res = load_sample(p)
    assert [r.vendor for r in res.rows] == ["C"]
    assert [e.row_number for e in res.errors] == [2, 3, 4]


def test_blank_rows_skipped_and_float_noise_does_not_leak(tmp_path):
    p = make(tmp_path, [["Vendor", "Amount", "Date"], [None, None, None], ["A", 0.1 + 0.2, "2025-01-01"]])
    res = load_sample(p)
    assert res.errors == [] and len(res.rows) == 1
    assert res.rows[0].amount == Decimal("0.3")


def test_missing_file(tmp_path):
    with pytest.raises(SampleFormatError):
        load_sample(tmp_path / "nope.xlsx")


def test_generated_sample_matches_labels():
    xlsx, labels = Path("data/generated/sample.xlsx"), Path("data/generated/labels/sample_labels.json")
    if not (xlsx.exists() and labels.exists()):
        pytest.skip("run python -m tools.datagen.generate first")
    res = load_sample(xlsx)
    expected = json.loads(labels.read_text())
    assert res.errors == []
    assert [r.row_id for r in res.rows] == [str(e["row_id"]) for e in expected]


def test_external_headers_and_text_row_ids_are_kept(tmp_path):
    p = make(tmp_path, [
        ["Row ID", "Vendor", "Invoice No.", "Invoice Date", "Amount (EUR)"],
        ["S-01", "Acme B.V.", " INV-2026-1037 ", date(2026, 1, 13), 2999.37],
        [7.0, "Beta", None, date(2026, 1, 14), 1],
        [None, "Gamma", "X-1", date(2026, 1, 15), 2],
    ])
    res = load_sample(p)
    assert res.errors == []
    assert [(r.row_id, r.reference) for r in res.rows] == [
        ("S-01", "INV-2026-1037"), ("7", None), ("4", "X-1")]
