"""Flexible input: any sheet layout, typed fields, column mappings, the value-free profile and the
Claude proposer (faked: no network). The live Claude call is in test_llm_live.py (marked llm)."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from pydantic import ValidationError

from tickmark.definitions import INVOICE_VOUCHING
from tickmark.extract import LabelExtractor
from tickmark.fields.llm_mapping import FakeProposer, _schema, request_payload, to_mapping
from tickmark.fields.mapping import (
    ColumnMapping,
    alias_mapping,
    apply_mapping,
    headers_sha256,
    resolve,
    validate,
)
from tickmark.fields.profile import profile_sheet, shape
from tickmark.fields.sheet import read_sheet
from tickmark.fields.types import parse_amount
from tickmark.pipeline import run_vouch
from tickmark.vouch import VouchPolicy
from tools.datagen.unfamiliar import EXPECTED, INJECTION, write_unfamiliar

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "data/generated"
Y2025 = VouchPolicy(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))


@pytest.fixture
def unfamiliar(tmp_path):
    if not (GEN / "sample.xlsx").exists():
        pytest.skip("generated data missing")
    write_unfamiliar(GEN / "sample.xlsx", tmp_path)
    return tmp_path / "sample.xlsx"


def confirmed(sheet, columns: dict[str, str]) -> ColumnMapping:
    return ColumnMapping(definition="invoice_vouching", version=1, columns=columns, source="person",
                         confirmed_by="test", headers_sha256=headers_sha256(sheet))


def make(tmp_path, rows) -> Path:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    p = tmp_path / "s.xlsx"
    wb.save(p)
    return p


def test_unknown_headers_are_not_guessed(unfamiliar):
    sheet = read_sheet(unfamiliar)
    mapping, missing = resolve(sheet, INVOICE_VOUCHING)
    assert mapping is None and missing == ["vendor", "amount", "invoice_date"]


def test_unfamiliar_sheet_mapped_gives_identical_verdicts(unfamiliar):
    sheet = read_sheet(unfamiliar)
    proposal = FakeProposer(EXPECTED).propose(sheet, INVOICE_VOUCHING)
    mapping = to_mapping(sheet, INVOICE_VOUCHING, proposal).model_copy(update={"confirmed_by": "test"})
    assert validate(sheet, INVOICE_VOUCHING, mapping) == []
    std = run_vouch(GEN / "sample.xlsx", GEN / "invoices", LabelExtractor(GEN / "labels"), Y2025)
    new = run_vouch(unfamiliar, GEN / "invoices", LabelExtractor(GEN / "labels"), Y2025, mapping)
    assert [(v.row_id, v.verdict, v.invoice_file, v.reasons) for v in new.verdicts] == \
           [(v.row_id, v.verdict, v.invoice_file, v.reasons) for v in std.verdicts]
    assert [r.invoice_file for r in new.results] == [r.invoice_file for r in std.results]


def test_validation_rejects_wrong_missing_and_double_columns(unfamiliar):
    sheet = read_sheet(unfamiliar)
    problems = validate(sheet, INVOICE_VOUCHING, confirmed(sheet, {"vendor": "B", "amount": "E", "invoice_date": "Z"}))
    assert "amount: only 0 of 16 values in column E read as money" in problems
    assert "invoice_date: column Z does not exist" in problems
    twice = validate(sheet, INVOICE_VOUCHING, confirmed(sheet, {"vendor": "B", "amount": "D", "invoice_date": "D"}))
    assert "column D is used for more than one field" in twice


def test_an_id_column_is_never_read_as_an_amount():
    assert pytest.raises(ValueError, parse_amount, "S-01")
    assert parse_amount("EUR 1.234,50") == Decimal("1234.50") and parse_amount("(50.00)") == Decimal("-50.00")


def test_raw_keeps_every_cell_with_duplicate_headers(unfamiliar):
    sheet = read_sheet(unfamiliar)
    res = apply_mapping(sheet, INVOICE_VOUCHING, confirmed(sheet, EXPECTED))
    row = res.rows[2]
    assert [c.col for c in row.raw] == list("ABCDEFGH")
    assert [c.header for c in row.raw][-2:] == ["Notes", "Notes"]  # a duplicate header survives
    assert row.raw[6].value == INJECTION  # kept as data, never acted on
    assert row.fields["amount"].value == str(row.amount) and row.fields["amount"].col == "D"


def test_profile_sends_no_cell_value(unfamiliar):
    sheet = read_sheet(unfamiliar)
    sent = json.dumps(request_payload(sheet, INVOICE_VOUCHING, with_values=False))
    values = {str(v).strip() for r in sheet.rows for v in r.values if v not in (None, "")}
    leaked = [v for v in values if len(v) >= 3 and v in sent]
    assert leaked == [], leaked
    assert INJECTION not in sent and "Pinewood" not in sent


def test_example_values_only_with_the_separate_setting(unfamiliar):
    sheet = read_sheet(unfamiliar)
    assert all(not c.get("examples") for c in request_payload(sheet, INVOICE_VOUCHING, False)["columns"])
    with_values = request_payload(sheet, INVOICE_VOUCHING, True)["columns"]
    assert all(len(c["examples"]) <= 5 for c in with_values) and any(c["examples"] for c in with_values)


def test_profile_describes_shapes():
    assert shape("INV-2026-1037") == "AAA-9999-9999"


def test_proposal_schema_allows_only_this_sheets_columns_and_our_fields(unfamiliar):
    model = _schema(read_sheet(unfamiliar), INVOICE_VOUCHING)
    ok = {"fields": [{"field": "amount", "column": "D", "confidence": 0.9, "reason": "r"}]}
    assert model.model_validate(ok)
    for bad in ({"field": "amount", "column": "Z"}, {"field": "delete_everything", "column": "A"}):
        with pytest.raises(ValidationError):
            model.model_validate({"fields": [bad | {"confidence": 1, "reason": "r"}]})


def test_an_injected_proposal_is_caught_by_validation(unfamiliar):
    """Whatever a proposer says, the values decide: 'amount = column A' (row numbers) fails."""
    sheet = read_sheet(unfamiliar)
    hijacked = to_mapping(sheet, INVOICE_VOUCHING, FakeProposer(EXPECTED | {"amount": "G"}).propose(sheet, INVOICE_VOUCHING))
    assert any(p.startswith("amount:") for p in validate(sheet, INVOICE_VOUCHING, hijacked))


def test_saved_mapping_only_applies_to_the_same_header_layout(tmp_path, unfamiliar):
    sheet = read_sheet(unfamiliar)
    other = read_sheet(make(tmp_path, [["Vendor", "Amount", "Date"], ["A", 1, "2025-01-01"]]))
    saved = confirmed(sheet, EXPECTED)
    assert resolve(sheet, INVOICE_VOUCHING, saved)[0].columns == EXPECTED
    assert resolve(other, INVOICE_VOUCHING, saved)[0].source == "aliases"  # different layout: not reused


def test_known_headers_still_map_automatically(tmp_path):
    sheet = read_sheet(make(tmp_path, [["Row ID", "Supplier", "Invoice No.", "Invoice Date", "Amount (EUR)"],
                                       ["S-1", "Acme", "X-1", "2025-01-01", 10]]))
    m = alias_mapping(sheet, INVOICE_VOUCHING)
    assert m.columns == {"row_id": "A", "vendor": "B", "reference": "C", "invoice_date": "D", "amount": "E"}


def test_profile_of_every_column(unfamiliar):
    cols = {p.col: p for p in profile_sheet(read_sheet(unfamiliar))}
    assert "money" in cols["D"].parses_as and "date" in cols["C"].parses_as
    assert any("comma decimal" in x for x in cols["D"].pattern)


def test_nothing_lost_check_reads_the_columns_the_mapping_names(unfamiliar):
    """Regression: the 'nothing lost, nothing changed' check must use the run's own mapping."""
    from tools.verify_clean import verify

    sheet = read_sheet(unfamiliar)
    assert verify(unfamiliar, mapping=confirmed(sheet, EXPECTED)) == []
