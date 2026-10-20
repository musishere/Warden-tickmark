import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tickmark.extract import DocIntError, DocIntExtractor
from tickmark.extract.azure_docint import parse_result

FIXTURE = Path(__file__).parent / "fixtures" / "docint_inv_2026_1222.json"


def test_parse_fields_confidence_and_snips():
    inv = parse_result(json.loads(FIXTURE.read_text()), "INV-2026-1222.pdf")
    assert {k: f.value for k, f in inv.fields.items()} == {
        "vendor": "Pinewood IT Solutions BV", "invoice_number": "INV-2026-1222",
        "invoice_date": "2026-01-16", "subtotal": "7092.84", "tax": "1489.5", "total": "8682.34"}
    total = inv.get("total")
    assert total.confidence == 0.95 and total.snip.text == "€ 8,682.34" and total.snip.page == 1
    # polygon in inches -> bbox in points
    assert (total.snip.bbox.x0, total.snip.bbox.y1) == pytest.approx((6.4 * 72, 5.2 * 72))


def test_missing_fields_are_absent_and_rest_wrapper_is_accepted():
    raw = {"analyzeResult": {"pages": [], "documents": [{"fields": {
        "InvoiceId": {"type": "string", "valueString": "A-1", "confidence": 0.99}}}]}}
    inv = parse_result(raw, "a.pdf")
    assert list(inv.fields) == ["invoice_number"] and inv.get("invoice_number").snip is None
    assert parse_result({"documents": []}, "b.pdf").fields == {}


def test_cached_result_is_used_without_credentials(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    cache = tmp_path / "cache"
    cache.mkdir()
    shutil.copy(FIXTURE, cache / f"{hashlib.sha256(pdf.read_bytes()).hexdigest()}.json")
    inv = DocIntExtractor(cache_dir=cache, endpoint="", key="").extract(pdf)
    assert inv.file == "x.pdf" and inv.get("invoice_number").value == "INV-2026-1222"


def test_uncached_without_credentials_fails_loudly(tmp_path):
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4 other")
    with pytest.raises(DocIntError, match="TICKMARK_DOCINT_ENDPOINT"):
        DocIntExtractor(cache_dir=tmp_path / "cache", endpoint="", key="").extract(pdf)


def test_line_items_are_parsed_with_their_lowest_confidence():
    inv = parse_result(json.loads(FIXTURE.read_text()), "INV-2026-1222.pdf")
    assert [(li.description, li.quantity, li.unit_price, li.amount) for li in inv.line_items] == [
        ("Cloud hosting (monthly)", "8", "371.42", "2971.36"), ("Data backup service", "7", "71.64", "501.48"),
        ("Laptop repair", "5", "431.98", "2159.9"), ("Cloud hosting (monthly)", "10", "146.01", "1460.1")]
    assert inv.line_items[0].confidence == 0.93 and inv.line_items[0].snip.page == 1
