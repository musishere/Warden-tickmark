import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tickmark.extract import LabelExtractor
from tickmark.models import (
    ExtractedField,
    ExtractedInvoice,
    ExtractedLineItem,
    Identification,
    SampleRow,
)
from tickmark.pipeline import run_vouch
from tickmark.sample import clean_sample
from tickmark.vouch import VouchPolicy, check_invoice, duplicate_map, vouch
from tickmark.vouch.compare import compare
from tools.eval_vouch import dataset_currency

ROOT = Path(__file__).resolve().parents[1]
GEN, EXT, HARD = ROOT / "data/generated", ROOT / "data/external/tickmark-sample", ROOT / "data/generated/hard"
Y2025 = VouchPolicy(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), currency="EUR", line_items=True)


def pdf(file="a.pdf", vendor="Acme B.V.", number="INV-1", d="2025-03-01", total="121.00", subtotal="100.00",
        tax="21.00", lines=(("2", "50.00", "100.00"),), confidence=1.0, currency="EUR"):
    values = {"vendor": vendor, "invoice_number": number, "invoice_date": d, "total": total,
              "subtotal": subtotal, "tax": tax, "currency": currency}
    return ExtractedInvoice(
        file=file, fields={k: ExtractedField(name=k, value=v, confidence=confidence) for k, v in values.items() if v},
        line_items=[ExtractedLineItem(description="x", quantity=q, unit_price=u, amount=a, confidence=confidence)
                    for q, u, a in lines])


def row(**kw):
    values = {"row_id": "1", "excel_row": 2, "vendor": "ACME BV", "amount": Decimal("121.00"),
              "invoice_date": date(2025, 3, 1)} | kw
    return clean_sample([SampleRow(**values)]).rows[0]


def linked(file="a.pdf"):
    return Identification(row_id="1", excel_row=2, status="linked", invoice_file=file)


def verdict(r, invoices, policy=Y2025, ident=None):
    return vouch([ident or linked(invoices[0].file)], [r], invoices, policy)[0]


def checks(inv, policy=Y2025, others=()):
    return {c.name: c for c in check_invoice(inv, policy, duplicate_map([inv, *others]))}


# --- row vs PDF ---------------------------------------------------------------------------------

def test_every_field_agrees():
    assert [(a.field, a.result) for a in compare(row(reference="inv 1"), pdf(), defaults={"currency": "EUR"})] == [
        ("number", "agrees"), ("vendor", "agrees"), ("amount", "agrees"), ("date", "agrees"), ("currency", "agrees")]


def test_differences_carry_the_exact_delta():
    got = {a.field: a for a in compare(row(amount=Decimal("371.00"), invoice_date=date(2025, 3, 4)), pdf())}
    assert (got["amount"].result, got["amount"].delta) == ("differs", "+250.00 (row − PDF)")
    assert (got["date"].result, got["date"].delta) == ("differs", "+3 days (row − PDF)")


def test_one_cent_off_is_a_difference_but_rounding_noise_is_not():
    assert compare(row(amount=Decimal("121.02")), pdf())[1].result == "differs"
    assert compare(row(amount=Decimal("121.01")), pdf())[1].result == "agrees"


def test_low_confidence_values_agree_when_equal_and_are_unverifiable_when_not():
    assert {a.result for a in compare(row(), pdf(confidence=0.5), defaults={"currency": "EUR"})} == {"agrees"}
    off = compare(row(amount=Decimal(1)), pdf(confidence=0.5), defaults={"currency": "EUR"})
    assert next(a for a in off if a.field == "amount").result == "unverifiable"  # not 'differs'
    assert verdict(row(amount=Decimal(1)), [pdf(confidence=0.5)]).verdict != "matched"


# --- the invoice on its own -----------------------------------------------------------------------

def test_clean_invoice_passes_every_check():
    assert {n: c.status for n, c in checks(pdf()).items()} == {
        "totals_reconcile": "pass", "lines_sum": "pass", "vat_rate": "pass", "in_period": "pass", "duplicate": "pass"}


def test_printed_total_that_does_not_add_up_fails():
    c = checks(pdf(total="221.00"))["totals_reconcile"]
    assert c.status == "fail" and "100.00 + 21.00 = 121.00 ≠ total 221.00" in c.detail


def test_lines_that_do_not_sum_or_multiply_fail():
    assert checks(pdf(lines=(("2", "50.00", "60.00"),)))["lines_sum"].status == "fail"
    assert "2 × 50.00 = 100.00 ≠ 99.00" in checks(pdf(lines=(("2", "50.00", "99.00"), ("1", "1.00", "1.00"))))[
        "lines_sum"].detail


def test_wrong_vat_fails_and_allowed_rates_pass():
    assert checks(pdf(tax="33.50", total="133.50"))["vat_rate"].status == "fail"
    assert checks(pdf(tax="9.00", total="109.00"))["vat_rate"].status == "pass"  # 9 % is allowed


def test_date_outside_the_period_fails_and_no_period_is_not_checkable():
    assert checks(pdf(d="2024-12-15"))["in_period"].status == "fail"
    assert checks(pdf(), VouchPolicy())["in_period"].status == "not_checkable"


def test_duplicates_same_vendor_fail_other_vendor_warns():
    assert checks(pdf(), others=[pdf("b.pdf")])["duplicate"].status == "fail"
    assert checks(pdf(), others=[pdf("b.pdf", vendor="Other Ltd")])["duplicate"].status == "warn"


def test_missing_values_make_checks_not_checkable():
    c = checks(pdf(subtotal="", lines=()))
    assert (c["totals_reconcile"].status, c["lines_sum"].status, c["vat_rate"].status) == (
        "not_checkable", "not_checkable", "not_checkable")


# --- verdict rules --------------------------------------------------------------------------------

def test_verdict_matched_only_when_everything_agrees_and_passes():
    assert verdict(row(), [pdf()]).verdict == "matched"


def test_a_difference_or_a_failed_check_is_a_difference():
    assert verdict(row(amount=Decimal(1)), [pdf()]).verdict == "difference"
    v = verdict(row(invoice_date=date(2024, 12, 15)), [pdf(d="2024-12-15")])  # row and PDF agree ...
    assert {a.result for a in v.agreements} == {"agrees"}
    assert v.verdict == "difference" and v.reasons == [  # ... but the invoice is outside the audit period
        "in_period failed: 2024-12-15 outside 2025-01-01 … 2025-12-31"]


def test_never_matched_on_missing_evidence():
    assert verdict(row(), [pdf()], VouchPolicy()).verdict == "needs_review"  # no currency to compare
    assert verdict(row(), [pdf(confidence=0.5)]).verdict == "needs_review"


def test_a_side_check_that_cannot_run_is_a_note_not_a_review():
    v = verdict(row(), [pdf(subtotal="", lines=())])  # the claim agrees; VAT and totals cannot be checked
    assert v.verdict == "matched" and any("vat_rate not checkable" in r for r in v.reasons)
    assert verdict(row(), [pdf()], VouchPolicy(currency="EUR")).verdict == "matched"  # no audit period: a note
    unsettled = vouch([linked()], [row()], [pdf(), pdf("b.pdf", vendor="")], Y2025)[0]
    assert unsettled.verdict == "needs_review"  # a possible double payment still needs a look


def test_difference_wins_over_unverifiable():
    assert verdict(row(amount=Decimal(1)), [pdf()], VouchPolicy()).verdict == "difference"


def test_a_warning_is_shown_but_does_not_block_matched():
    v = vouch([linked()], [row()], [pdf(), pdf("b.pdf", vendor="Other Ltd")], Y2025)[0]
    assert v.verdict == "matched" and any("duplicate warning" in r for r in v.reasons)


def test_unresolved_identification_is_not_compared():
    for status in ("needs_review", "no_evidence"):
        v = verdict(row(), [pdf()], ident=Identification(row_id="1", excel_row=2, status=status, reason="x"))
        assert v.verdict == status and v.agreements == [] and v.checks == []


# --- the datasets ------------------------------------------------------------------------------

def _run(base, period):
    if not (base / "sample.xlsx").exists():
        pytest.skip(f"{base} missing")
    return run_vouch(base / "sample.xlsx", base / "invoices", LabelExtractor(base / "labels"),
                     VouchPolicy(period_start=period[0], period_end=period[1], currency=dataset_currency(base),
                                 line_items=True))


def test_external_pack_matches_its_expected_results():
    run = _run(EXT, (date(2026, 1, 1), date(2026, 12, 31)))
    expected = {e["row_id"]: e["expected_status"] for e in json.loads((EXT / "expected_results.json").read_text())}
    assert {v.row_id: v.verdict for v in run.verdicts} == expected
    s06 = next(v for v in run.verdicts if v.row_id == "S-06")
    assert any("7092.84 + 1489.50 = 8582.34 ≠ total 8682.34" in r for r in s06.reasons)


def test_generated_verdicts_follow_sample_labels_and_invoice_defects():
    run = _run(GEN, (date(2025, 1, 1), date(2025, 12, 31)))
    labels = {str(s["row_id"]): s for s in json.loads((GEN / "labels/sample_labels.json").read_text())}
    defects = {p.stem + ".pdf": json.loads(p.read_text())["defects"] for p in (GEN / "labels").glob("invoice_*.json")}
    for v in run.verdicts:
        lab = labels[v.row_id]
        want = ("no_evidence" if lab["expected"] == "no_evidence" else
                "difference" if lab["expected"] == "difference" or defects[lab["invoice_file"]] else "matched")
        assert v.verdict == want, (v.row_id, v.reasons)
        if lab["difference_field"]:
            assert any(a.field == lab["difference_field"] and a.result == "differs" for a in v.agreements)


def test_invoice_checks_find_exactly_the_labelled_defects():
    run = _run(GEN, (date(2025, 1, 1), date(2025, 12, 31)))
    dups = duplicate_map(run.extracted)
    by_check = {"lines_sum": "lines_dont_sum", "vat_rate": "vat_wrong", "in_period": "date_out_of_period",
                "duplicate": "duplicate_number", "totals_reconcile": "totals_dont_reconcile"}
    labels = {p.stem + ".pdf": json.loads(p.read_text()) for p in (GEN / "labels").glob("invoice_*.json")}
    reused = {lab["invoice"]["invoice_number"] for lab in labels.values() if "duplicate_number" in lab["defects"]}
    for inv in run.extracted:
        found = {by_check[c.name] for c in check_invoice(inv, run.policy, dups) if c.status in ("fail", "warn")}
        want = set(labels[inv.file]["defects"])
        if labels[inv.file]["invoice"]["invoice_number"] in reused:  # a duplicate involves both invoices
            want.add("duplicate_number")
        assert found == want, inv.file


def test_hard_set_links_are_all_matched():
    run = _run(HARD, (date(2025, 1, 1), date(2025, 12, 31)))
    linked_rows = {r.row_id for r in run.results if r.status == "linked"}
    assert {v.row_id for v in run.verdicts if v.verdict == "matched"} == linked_rows


# --- currency ------------------------------------------------------------------------------------

def with_currency(r, code):
    r.row.set("currency", code)
    return r


def test_same_number_in_another_currency_is_a_difference():
    v = verdict(with_currency(row(), "USD"), [pdf()])  # 121.00 USD vs 121.00 EUR
    assert v.verdict == "difference" and "currency differs: row USD, PDF EUR" in v.reasons


def test_the_engagement_currency_is_used_when_the_row_has_none():
    v = verdict(row(), [pdf()])
    assert v.verdict == "matched"
    assert next(a for a in v.agreements if a.field == "currency").row_value == "EUR (the engagement's)"
    assert verdict(row(), [pdf()], Y2025.model_copy(update={"currency": "GBP"})).verdict == "difference"


def test_never_matched_without_a_currency_on_either_side():
    no_currency = Y2025.model_copy(update={"currency": None})
    assert verdict(row(), [pdf()], no_currency).verdict == "needs_review"  # the row (and engagement) say none
    assert verdict(row(), [pdf(currency="")]).verdict == "needs_review"  # the PDF says none


def test_vat_with_a_breakdown_per_rate_and_without_one():
    mixed = pdf(subtotal="473.00", tax="56.87", total="529.87")  # 19 % and 7 % on one invoice
    y = Y2025.model_copy(update={"vat_rates": [Decimal(0), Decimal("0.07"), Decimal("0.19")]})
    assert checks(mixed, y)["vat_rate"].status == "not_checkable"  # 12.02 %: between rates, no breakdown
    mixed.tax_details = [{"rate": "7", "amount": "19.25"}, {"rate": "19", "amount": "37.62"}]
    assert checks(mixed, y)["vat_rate"].status == "pass"
    mixed.tax_details = [{"rate": "7", "amount": "19.25"}, {"rate": "16", "amount": "37.62"}]
    assert "rate 16% not allowed" in checks(mixed, y)["vat_rate"].detail
    one_wrong = pdf(subtotal="600.00", tax="114.00", total="714.00")  # 19 %, allowed 0/9/21: one rate, wrong
    assert checks(one_wrong)["vat_rate"].status == "fail"


def test_a_date_that_reads_both_ways_and_a_partly_read_name_are_not_differences():
    swapped = compare(row(invoice_date=date(2025, 3, 11)), pdf(d="2025-11-03"), defaults={"currency": "EUR"})
    assert next(a for a in swapped if a.field == "date").result == "unverifiable"
    late = compare(row(invoice_date=date(2025, 3, 14)), pdf(d="2025-03-11"), defaults={"currency": "EUR"})
    assert next(a for a in late if a.field == "date").result == "differs"  # 3 days: a real difference
    part = compare(row(vendor="MVM Musterhafter Versicherungsverein"), pdf(vendor="Versicherungsverein"),
                   defaults={"currency": "EUR"})
    assert next(a for a in part if a.field == "vendor").result == "unverifiable"
    other = compare(row(vendor="Globex"), pdf(vendor="Initech"), defaults={"currency": "EUR"})
    assert next(a for a in other if a.field == "vendor").result == "differs"
