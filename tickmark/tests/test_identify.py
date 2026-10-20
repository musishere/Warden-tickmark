import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tickmark.extract import LabelExtractor, extract_all
from tickmark.match import identify
from tickmark.models import ExtractedField, ExtractedInvoice, SampleRow
from tickmark.sample import clean_sample, load_sample

GEN = Path("data/generated")
EXT = Path("data/external/tickmark-sample")


def pdf(file, vendor="Acme B.V.", number="INV-1", d="2025-03-01", total="121.00",
        subtotal="100.00", tax="21.00", confidence=1.0):
    values = {"vendor": vendor, "invoice_number": number, "invoice_date": d, "total": total,
              "subtotal": subtotal, "tax": tax}
    return ExtractedInvoice(file=file, fields={
        k: ExtractedField(name=k, value=v, confidence=confidence) for k, v in values.items() if v})


def rows(*specs):
    out = []
    for i, s in enumerate(specs, start=1):
        s = {"vendor": "Acme BV", "amount": "121.00", "invoice_date": date(2025, 3, 1), **s}
        out.append(SampleRow(row_id=str(i), excel_row=i + 1, **{**s, "amount": Decimal(s["amount"])}))
    return clean_sample(out).rows


def one(row_spec, *pdfs):
    return identify(rows(row_spec), list(pdfs))[0]


def test_number_plus_one_field_links():
    r = one({"reference": "inv 1", "amount": "999"}, pdf("a.pdf"), pdf("b.pdf", number="INV-2"))
    assert (r.status, r.invoice_file) == ("linked", "a.pdf")
    assert any("invoice number" in e for e in r.evidence)


def test_number_alone_is_not_enough():
    r = one({"reference": "INV-1", "vendor": "Other Ltd", "amount": "5", "invoice_date": date(2024, 1, 1)},
            pdf("a.pdf"))
    assert r.status == "needs_review" and "only the invoice number" in r.reason


def test_conflicting_number_never_links():
    r = one({"reference": "INV-7"}, pdf("a.pdf"))
    assert r.status == "needs_review" and "number differs" in r.reason


def test_vendor_alone_is_not_enough():
    assert one({"amount": "5", "invoice_date": date(2024, 1, 1)}, pdf("a.pdf")).status == "no_evidence"


def test_vendor_and_date_link_even_if_amount_differs():
    r = one({"amount": "146.00"}, pdf("a.pdf"), pdf("b.pdf", d="2025-04-01"))
    assert (r.status, r.invoice_file) == ("linked", "a.pdf")


def test_lookalike_vendor_never_links():
    r = one({"vendor": "Acme Foods"}, pdf("a.pdf", vendor="Acme Food"))
    assert r.status == "needs_review" and "only similar" in r.reason


def test_two_equally_good_pdfs_go_to_review():
    r = one({"invoice_date": date(2025, 3, 5)}, pdf("a.pdf"), pdf("b.pdf", d="2025-03-09"))
    assert r.status == "needs_review" and "ambiguous" in r.reason
    assert {c.invoice_file for c in r.candidates} == {"a.pdf", "b.pdf"}


def test_more_agreeing_fields_wins():
    r = one({}, pdf("a.pdf"), pdf("b.pdf", d="2025-03-09"))
    assert (r.status, r.invoice_file) == ("linked", "a.pdf")


def test_one_pdf_backs_one_row():
    res = identify(rows({}, {}), [pdf("a.pdf")])
    assert [r.status for r in res] == ["needs_review", "needs_review"]
    assert "claimed by several rows" in res[0].reason


def test_strictly_better_row_keeps_contested_pdf():
    res = identify(rows({}, {"amount": "1"}), [pdf("a.pdf")])
    assert [(r.status, r.invoice_file) for r in res] == [("linked", "a.pdf"), ("needs_review", None)]


def test_low_confidence_fields_are_not_evidence():
    r = one({}, pdf("a.pdf", confidence=0.5))
    assert r.status == "no_evidence"


def test_total_that_does_not_reconcile_is_not_evidence():
    # vendor + amount would link, but the printed total != subtotal + tax
    r = one({"invoice_date": date(2024, 1, 1)}, pdf("a.pdf", total="121.00", tax="25.00"))
    assert r.status == "no_evidence"


def _run(sample, invoices, labels):
    if not Path(sample).exists():
        pytest.skip("sample data not present")
    return identify(clean_sample(load_sample(sample).rows).rows,
                    extract_all(LabelExtractor(labels), invoices))


def test_generated_set_links_every_row_to_its_labelled_pdf():
    res = _run(GEN / "sample.xlsx", GEN / "invoices", GEN / "labels")
    truth = {str(t["row_id"]): t["invoice_file"]
             for t in json.loads((GEN / "labels" / "sample_labels.json").read_text())}
    assert {r.row_id: r.invoice_file for r in res} == truth
    assert all(r.status == ("linked" if truth[r.row_id] else "no_evidence") for r in res)


def test_external_pack_links_by_invoice_number():
    res = _run(EXT / "sample.xlsx", EXT / "invoices", EXT / "labels")
    got = {r.row_id: r.invoice_file for r in res}
    assert got == {f"S-0{i}": f"INV-2026-{n}.pdf" for i, n in
                   zip(range(1, 7), (1037, 1074, 1111, 1148, 1185, 1222))} | {"S-07": None}


def test_hard_negatives_never_link_wrong_and_get_their_safe_outcome(tmp_path):
    from tools.datagen.hard import write_hard_set

    write_hard_set(tmp_path)
    res = _run(tmp_path / "sample.xlsx", tmp_path / "invoices", tmp_path / "labels")
    truth = {t["row_id"]: t for t in json.loads((tmp_path / "truth.json").read_text())}
    outcome = {"linked": "link", "needs_review": "review", "no_evidence": "none"}
    for r in res:
        t = truth[r.row_id]
        if r.status == "linked":
            assert r.invoice_file == t["invoice_file"], (r.row_id, t["tests"])
        assert outcome[r.status] == t["expect"], (r.row_id, t["tests"], r.reason)


def test_every_decision_is_traced():
    pdfs = [pdf("a.pdf"), pdf("b.pdf", vendor="Other Ltd", d="2024-01-01", total="1", subtotal="1", tax="0")]
    r = identify(rows({}), pdfs)[0]
    assert [c.invoice_file for c in r.comparisons] == ["a.pdf", "b.pdf"]
    assert [k.result for k in r.comparisons[0].checks] == ["not in row", "equal", "equal", "equal"]
    assert r.comparisons[1].outcome.startswith("not a candidate")
    assert r.steps[-1].endswith("→ linked")
