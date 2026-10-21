"""Reading PDFs from their own text, and routing to Azure only where that is not reliable.
Azure is faked (no network): it answers from the label JSON or with fixed fields."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from tickmark.extract import LabelExtractor
from tickmark.extract.local import LocalExtractor
from tickmark.extract.router import Router
from tickmark.extract.text import found_in_text
from tickmark.models import ExtractedField, ExtractedInvoice
from tickmark.pipeline import run_vouch
from tickmark.vouch import VouchPolicy

ROOT = Path(__file__).resolve().parents[1]
GEN, HARD = ROOT / "data/generated", ROOT / "data/generated/hard"
Y2025 = VouchPolicy(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), currency="EUR")
W, H = A4


def pdf(path: Path, lines: list[tuple[float, float, str]], image: bool = False, rotate: bool = False) -> Path:
    """A one-page PDF with text at (x, y from the top); optionally over a full-page image, or rotated."""
    c = canvas.Canvas(str(path), pagesize=A4)
    if image:
        img = path.with_suffix(".png")
        Image.new("RGB", (595, 842), "white").save(img)
        c.drawImage(str(img), 0, 0, W, H)
    if rotate:
        c.translate(W, 0)
        c.rotate(90)
    for x, y, text in lines:
        c.drawString(x, H - y, text)
    c.save()
    return path


def scan(path: Path, text: str = "Invoice No: INV-1  Total 121.00") -> Path:
    """A page that is only a picture of text: what a scanner or phone produces."""
    img = path.with_suffix(".png")
    im = Image.new("RGB", (1240, 1754), "white")
    ImageDraw.Draw(im).text((100, 100), text, fill="black")
    im.save(img)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(str(img), 0, 0, W, H)
    c.save()
    return path


def invoice(path: Path, vendor="Acme B.V.", number="INV-1", d="2025-03-01", net="100.00", vat="21.00",
            total="121.00", extra=()) -> Path:
    return pdf(path, [(50, 60, vendor), (400, 60, f"Invoice No: {number}"), (400, 76, f"Date: {d}"),
                      (360, 300, "Net total"), (460, 300, net), (360, 316, "VAT 21%"), (460, 316, vat),
                      (360, 332, "Total (EUR)"), (460, 332, total), *extra])


def read(path: Path) -> ExtractedInvoice:
    return LocalExtractor().extract(path)


def values(inv: ExtractedInvoice) -> dict[str, str]:
    return {k: f.value for k, f in inv.fields.items()}


# --- values next to labels -------------------------------------------------------------------------

def test_generated_invoices_read_locally_equal_their_labels():
    for base in (GEN, HARD):
        if not (base / "invoices").exists():
            pytest.skip("generated data missing")
        for f in sorted((base / "invoices").glob("*.pdf")):
            local, truth = read(f), LabelExtractor(base / "labels").extract(f)
            assert not local.needs_ocr and not local.unread, f.name
            for name, field in local.fields.items():
                want = truth.fields[name].value
                assert (Decimal(field.value) == Decimal(want)) if name in ("total", "subtotal", "tax") \
                    else field.value == want, (f.name, name)


def test_values_next_to_their_labels_with_where_they_are(tmp_path):
    inv = read(invoice(tmp_path / "a.pdf"))
    assert values(inv) == {"invoice_number": "INV-1", "invoice_date": "2025-03-01", "subtotal": "100.00",
                           "tax": "21.00", "total": "121.00", "currency": "EUR"}
    assert inv.fields["total"].snip.page == 1 and inv.fields["total"].snip.text == "121.00"


def test_the_longest_label_wins_so_net_is_not_read_as_total(tmp_path):
    inv = read(pdf(tmp_path / "a.pdf", [(300, 300, "Total excl. VAT"), (460, 300, "100.00"),
                                        (300, 316, "VAT"), (460, 316, "21.00"),
                                        (300, 332, "Total incl. VAT"), (460, 332, "121.00")]))
    assert values(inv) | {} == {"subtotal": "100.00", "tax": "21.00", "total": "121.00"}


def test_two_different_totals_are_not_read(tmp_path):
    inv = read(pdf(tmp_path / "a.pdf", [(300, 300, "Total"), (460, 300, "100.00"),
                                        (300, 500, "Total"), (460, 500, "120.00")]))
    assert "total" not in inv.fields and inv.unread["total"].startswith("ambiguous: 100.00")


def test_european_amounts_dates_in_words_and_currency_symbols(tmp_path):
    inv = read(pdf(tmp_path / "a.pdf", [(50, 100, "Invoice date: 19 August 2025"), (300, 300, "Subtotal"),
                                        (460, 300, "1.020,25"), (300, 332, "Totaal"), (460, 332, "€ 1.234,50")]))
    assert values(inv) == {"invoice_date": "2025-08-19", "subtotal": "1020.25", "total": "1234.50",
                           "currency": "EUR"}


def test_a_date_that_reads_two_ways_is_not_read(tmp_path):
    assert "invoice_date" not in read(pdf(tmp_path / "a.pdf", [(50, 100, "Date: 03/04/2025")])).fields


def test_vendor_counts_only_in_the_letterhead_never_as_the_customer(tmp_path):
    inv = read(pdf(tmp_path / "a.pdf", [(50, 60, "Globex Ltd"), (50, 120, "Bill to:"), (50, 136, "Acme B.V."),
                                        (300, 332, "Total"), (460, 332, "121.00")]))
    assert found_in_text(inv, "name", "party", "GLOBEX LIMITED") is not None
    assert found_in_text(inv, "name", "party", "Acme BV") is None  # the customer, not the issuer
    assert found_in_text(inv, "name", "party", "Globex") is not None  # legal form ignored
    labs = read(pdf(tmp_path / "b.pdf", [(50, 60, "Contoso Labs Ltd"), (300, 332, "Total"), (460, 332, "121.00")]))
    assert found_in_text(labs, "name", "party", "Contoso Ltd") is None  # a look-alike is not the vendor
    assert found_in_text(inv, "identifier", "key", "inv 1") is None


# --- the file check: which PDFs cannot be read from their own text ----------------------------------

def test_a_scan_has_no_text_layer(tmp_path):
    inv = read(scan(tmp_path / "s.pdf"))
    assert inv.fields == {} and inv.needs_ocr == ["page 1: no text layer (a scan, a photo or text drawn as shapes)"]


def test_text_over_a_full_page_image_is_a_scanners_ocr(tmp_path):
    inv = read(pdf(tmp_path / "o.pdf", [(50, 60 + 16 * i, "Invoice No: INV-1 Total 121.00") for i in range(5)],
                   image=True))
    assert inv.needs_ocr and "scanner's OCR" in inv.needs_ocr[0]


def test_rotated_text_needs_ocr(tmp_path):
    inv = read(pdf(tmp_path / "r.pdf", [(50, 60 + 16 * i, "Invoice No: INV-1 Total 121.00") for i in range(5)],
                   rotate=True))
    assert inv.needs_ocr and "rotated" in inv.needs_ocr[0]


def test_a_blank_page_is_fine(tmp_path):
    assert read(pdf(tmp_path / "b.pdf", [])).needs_ocr == []


# --- routing ----------------------------------------------------------------------------------------

class FakeAzure:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def extract(self, p: Path) -> ExtractedInvoice:
        self.calls.append(p.name)
        return self.answer(p)


def sample(tmp_path, rows) -> Path:
    wb = Workbook()
    wb.active.append(["Row", "Vendor", "Invoice No", "Amount", "Date"])
    for r in rows:
        wb.active.append(r)
    wb.save(tmp_path / "s.xlsx")
    return tmp_path / "s.xlsx"


def azure_answer(**fields):
    return lambda p: ExtractedInvoice(file=p.name, extractor="azure-docint", fields={
        k: ExtractedField(name=k, value=v, confidence=0.99) for k, v in fields.items()})


def test_auto_reads_clean_pdfs_locally_and_matches_without_azure(tmp_path):
    (tmp_path / "inv").mkdir()
    invoice(tmp_path / "inv/a.pdf")
    azure = FakeAzure(azure_answer())
    run = run_vouch(sample(tmp_path, [[1, "ACME BV", "INV-1", 121, "2025-03-01"]]), tmp_path / "inv",
                    Router("auto", azure), Y2025)
    assert [v.verdict for v in run.verdicts] == ["matched"] and azure.calls == []
    assert run.routes == {"a.pdf": ("local", "")}


def test_a_scan_goes_to_azure_in_auto_and_is_unread_with_the_reason_when_off(tmp_path):
    (tmp_path / "inv").mkdir()
    scan(tmp_path / "inv/a.pdf")
    rows = [[1, "Acme B.V.", "INV-1", 121, "2025-03-01"]]
    azure = FakeAzure(azure_answer(vendor="Acme B.V.", invoice_number="INV-1", invoice_date="2025-03-01",
                                   subtotal="100.00", tax="21.00", total="121.00", currency="EUR"))
    run = run_vouch(sample(tmp_path, rows), tmp_path / "inv", Router("auto", azure), Y2025)
    assert azure.calls == ["a.pdf"] and run.routes["a.pdf"][0] == "azure"
    assert [v.verdict for v in run.verdicts] == ["matched"]
    off = run_vouch(sample(tmp_path, rows), tmp_path / "inv", Router("off", FakeAzure(azure_answer())), Y2025)
    assert off.verdicts[0].verdict == "no_evidence"
    assert "not read (needs OCR; Azure is off or not available): a.pdf (page 1: no text layer" \
        in off.verdicts[0].reasons[-1]


def test_a_value_not_read_clearly_sends_only_that_pdf_to_azure(tmp_path):
    (tmp_path / "inv").mkdir()
    invoice(tmp_path / "inv/a.pdf")
    invoice(tmp_path / "inv/b.pdf", vendor="Globex Ltd", number="INV-2", extra=[(360, 500, "Total"), (460, 500, "99.00")])
    rows = [[1, "Acme", "INV-1", 121, "2025-03-01"], [2, "Globex", "INV-2", 121, "2025-03-01"]]
    azure = FakeAzure(azure_answer(invoice_number="INV-2", total="121.00", subtotal="100.00", tax="21.00"))
    run = run_vouch(sample(tmp_path, rows), tmp_path / "inv", Router("auto", azure), Y2025)
    assert azure.calls == ["b.pdf"]  # b has two different totals; a was settled locally
    assert run.routes["b.pdf"] == ("azure", "not read clearly: total")
    assert [v.verdict for v in run.verdicts] == ["matched", "matched"]


def test_local_and_azure_reading_differently_is_trusted_by_neither(tmp_path):
    (tmp_path / "inv").mkdir()
    invoice(tmp_path / "inv/a.pdf")
    router = Router("always", FakeAzure(azure_answer(invoice_number="INV-1", total="112.00")))
    run = run_vouch(sample(tmp_path, [[1, "Acme", "INV-1", 121, "2025-03-01"]]), tmp_path / "inv", router, Y2025)
    inv = run.extracted[0]
    assert "total" not in inv.fields and inv.unread["total"] == "read differently: Azure '112.00', local '121.00'"
    assert run.verdicts[0].verdict != "matched"


def test_item_lines_are_checked_only_when_the_engagement_asks(tmp_path):
    (tmp_path / "inv").mkdir()
    invoice(tmp_path / "inv/a.pdf")
    rows = [[1, "Acme", "INV-1", 121, "2025-03-01"]]
    azure = FakeAzure(azure_answer())
    off = run_vouch(sample(tmp_path, rows), tmp_path / "inv", Router("auto", azure), Y2025)
    assert "lines_sum" not in {c.name for c in off.verdicts[0].checks} and azure.calls == []
    on = run_vouch(sample(tmp_path, rows), tmp_path / "inv", Router("auto", azure),
                   Y2025.model_copy(update={"line_items": True}))
    assert azure.calls == ["a.pdf"] and on.routes["a.pdf"] == ("azure", "not read clearly: line items")


def test_a_bad_azure_setting_is_refused():
    with pytest.raises(ValueError, match="auto, off or always"):
        Router("sometimes", FakeAzure(azure_answer()))


def test_without_azure_credentials_auto_keeps_the_local_reading_and_says_why(tmp_path, monkeypatch):
    from tickmark.config import settings
    from tickmark.extract import DocIntExtractor

    monkeypatch.setattr(settings, "docint_endpoint", "")
    (tmp_path / "inv").mkdir()
    scan(tmp_path / "inv/a.pdf")
    run = run_vouch(sample(tmp_path, [[1, "Acme", "INV-1", 121, "2025-03-01"]]), tmp_path / "inv",
                    Router("auto", DocIntExtractor(cache_dir=tmp_path / "cache")), Y2025)
    assert run.routes["a.pdf"][0] == "local" and "not available" in run.routes["a.pdf"][1]
    assert run.verdicts[0].verdict == "no_evidence"
