import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from tickmark.models import SampleRow
from tickmark.sample import clean_sample, load_sample, normalize_vendor
from tools.datagen.messy import write_messy_sample


@pytest.mark.parametrize("name", ["Acme B.V.", "ACME BV", "acme  b.v", " Acme Ltd. ", "Acme GmbH & Co. KG"])
def test_vendor_variants_share_a_key(name):
    assert normalize_vendor(name) == "acme"


def test_vendor_key_details():
    assert normalize_vendor("Müller Büro GmbH") == "muller buro"
    assert normalize_vendor("Proseware Print & Media") == "proseware print and media"
    assert normalize_vendor("GmbH") == "gmbh"  # suffix-only name keeps itself
    assert normalize_vendor("Acme Foods") != normalize_vendor("Acme Fuels")


def row(i, vendor="Acme", amount="10", d=date(2025, 1, 1)):
    return SampleRow(row_id=i, vendor=vendor, amount=Decimal(amount), invoice_date=d)


def test_duplicate_flagged_on_later_row_only_and_input_unchanged():
    rows = [row(1, "Acme B.V."), row(2, "ACME BV"), row(3, amount="11")]
    before = [r.model_copy() for r in rows]
    res = clean_sample(rows)
    assert [cr.flags for cr in res.rows] == [[], ["duplicate_of:1"], []]
    assert rows == before


def test_negative_zero_and_period_flags_have_notes():
    res = clean_sample(
        [row(1, amount="-5"), row(2, amount="0", d=date(2024, 12, 31))],
        period=(date(2025, 1, 1), date(2025, 12, 31)),
    )
    assert res.rows[0].flags == ["negative_amount"]
    assert res.rows[1].flags == ["zero_amount", "date_outside_period"]
    assert sorted(n.rule for n in res.notes) == sorted(f for cr in res.rows for f in cr.flags)


def make(tmp_path: Path, rows: list[list]) -> Path:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    p = tmp_path / "s.xlsx"
    wb.save(p)
    return p


def test_loader_cleanup(tmp_path):
    p = make(tmp_path, [
        ["Vendor", "Amount", "Date"],
        ["A\xa0\xa0B​", "(50.00)", "31.12.2025"],
        ["C", "123.45-", "2025-01-01"],
        ["D", 1, "03/04/2025"],
        ["Total", 51, None],
    ])
    res = load_sample(p)
    assert [(r.vendor, r.amount, r.invoice_date) for r in res.rows] == [
        ("A B", Decimal("-50.00"), date(2025, 12, 31)),
        ("C", Decimal("-123.45"), date(2025, 1, 1)),
    ]
    assert [e.row_number for e in res.errors] == [4] and "ambiguous" in res.errors[0].message
    assert [s.row_number for s in res.skipped] == [5]


def test_messy_sheet_matches_labels(tmp_path):
    write_messy_sample(tmp_path)
    labels = json.loads((tmp_path / "labels" / "sample_messy_labels.json").read_text())
    loaded = load_sample(tmp_path / "sample_messy.xlsx")
    cleaned = {cr.row.excel_row: cr for cr in clean_sample(loaded.rows).rows}
    errors = {e.row_number: e.message for e in loaded.errors}
    skipped = {s.row_number: s.reason for s in loaded.skipped}
    for exp in labels:
        n = exp["excel_row"]
        if exp["outcome"] == "loaded":
            cr = cleaned[n]
            assert (cr.vendor_key, cr.row.amount, cr.flags) == (
                exp["vendor_key"], Decimal(exp["amount"]), exp["flags"]), n
        elif exp["outcome"] == "error":
            assert exp["reason"] in errors[n]
        elif exp["outcome"] == "skipped":
            assert exp["reason"] in skipped[n]
        else:
            assert n not in cleaned and n not in errors and n not in skipped


def test_clean_generated_sample_has_no_flags():
    p = Path("data/generated/sample.xlsx")
    if not p.exists():
        pytest.skip("run python -m tools.datagen.generate first")
    assert all(not cr.flags for cr in clean_sample(load_sample(p).rows).rows)


def test_verify_clean_passes_on_messy_sheet(tmp_path):
    from tools.verify_clean import verify

    write_messy_sample(tmp_path)
    assert verify(tmp_path / "sample_messy.xlsx") == []


@pytest.mark.parametrize("tamper, expect", [
    (lambda res: res.rows.pop(), "LOST"),
    (lambda res: setattr(res.rows[2], "amount", res.rows[2].amount + Decimal("0.01")), "amount changed"),
    (lambda res: setattr(res.rows[2], "amount", res.rows[2].amount / 10), "amount changed"),
    (lambda res: setattr(res.rows[0], "vendor", "Northwind Logistic B.V."), "vendor changed"),
    (lambda res: setattr(res.rows[0], "invoice_date", date(2025, 1, 3)), "date changed"),
    (lambda res: res.errors.append(res.errors[0]), "accounted for 2 times"),
])
def test_verify_clean_catches_tampering(tmp_path, monkeypatch, tamper, expect):
    import tools.verify_clean as vc

    write_messy_sample(tmp_path)

    def tampered(path, **kw):
        res = load_sample(path, **kw)
        tamper(res)
        return res

    monkeypatch.setattr(vc, "load_sample", tampered)
    assert any(expect in p for p in vc.verify(tmp_path / "sample_messy.xlsx"))
