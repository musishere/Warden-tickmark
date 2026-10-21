"""Real invoices with known answers: ZUGFeRD / Factur-X PDFs carry their own data as embedded XML.

    git clone --depth 1 https://github.com/ZUGFeRD/corpus.git data/external/zugferd-corpus
    python -m tools.datagen.facturx [--corpus data/external/zugferd-corpus] [--out data/real/facturx]
    python -m tools.eval_reader data/real/facturx-all   # reading accuracy on every rendering

For each PDF outside the corpus's fail/ folders with an embedded invoice XML (ZUGFeRD 1/2, Factur-X,
XRechnung CII): the XML is the truth -> labels/<file>.json (the shape tickmark.extract.LabelExtractor
reads). The PDF is copied under a short name. Then a sample sheet like an auditor's: one row per
invoice, with seeded mistakes - an amount, a date or a vendor changed, and rows whose invoice is
not in the folder - and expected.json saying what each row should get from matching.

The corpus files are untrusted input: XML is parsed with the standard library (expat guards against
entity expansion), nothing from it is executed, file names are not reused.
"""
import argparse
import json
import random
import shutil
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pypdfium2 as pdfium
from openpyxl import Workbook

XML_NAMES = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml")


def embedded_xml(pdf: Path) -> bytes | None:
    try:
        doc = pdfium.PdfDocument(str(pdf))
        for i in range(doc.count_attachments()):
            a = doc.get_attachment(i)
            if a.get_name().lower() in XML_NAMES:
                return bytes(a.get_data())
    except Exception:  # noqa: BLE001 - a broken file is skipped, not fatal
        return None
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node, *path):
    """First descendant following the local names in path (namespaces ignored: ZUGFeRD 1 and 2 differ)."""
    for name in path:
        node = next((e for e in node.iter() if _local(e.tag) == name and e is not node), None)
        if node is None:
            return None
    return node


def _text(node, *path) -> str | None:
    n = _find(node, *path)
    return n.text.strip() if n is not None and n.text and n.text.strip() else None


def _amount(node, name: str, currency: str | None) -> str | None:
    """A monetary summation amount; with several (e.g. tax in two currencies), the invoice currency's."""
    found = [e for e in node.iter() if _local(e.tag) == name and e.text]
    if currency:
        found = [e for e in found if e.get("currencyID") in (None, currency)] or found
    try:
        return f"{Decimal(found[0].text.strip()):.2f}" if found else None
    except InvalidOperation:
        return None


def parse(xml: bytes) -> dict | None:
    """The invoice's truth from its CII XML, or None if the essentials are missing."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    doc = _find(root, "ExchangedDocument")
    doc = doc if doc is not None else _find(root, "HeaderExchangedDocument")
    if doc is None:
        return None
    raw_date = _text(doc, "IssueDateTime", "DateTimeString")
    currency = _text(root, "InvoiceCurrencyCode")
    summation = _find(root, "SpecifiedTradeSettlementHeaderMonetarySummation")
    summation = summation if summation is not None else _find(root, "SpecifiedTradeSettlementMonetarySummation")
    truth = {
        "invoice_number": _text(doc, "ID"),
        "type_code": _text(doc, "TypeCode"),
        "invoice_date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if raw_date and len(raw_date) >= 8 else None,
        "vendor": _text(root, "SellerTradeParty", "Name"),
        "customer": _text(root, "BuyerTradeParty", "Name"),
        "currency": currency,
        "net_total": _amount(summation, "TaxBasisTotalAmount", currency) if summation is not None else None,
        "vat_amount": _amount(summation, "TaxTotalAmount", currency) if summation is not None else None,
        "total": _amount(summation, "GrandTotalAmount", currency) if summation is not None else None,
        "po_number": _text(root, "BuyerOrderReferencedDocument", "IssuerAssignedID"),
        "vat_rates": sorted({e.text.strip() for e in root.iter() if _local(e.tag) == "RateApplicablePercent"
                             and e.text}),
    }
    return truth if all(truth[k] for k in ("invoice_number", "invoice_date", "vendor", "total")) else None


def build(corpus: Path, out: Path, seed: int = 7, unique: bool = True) -> dict:
    """unique: one PDF per invoice (the corpus renders many invoices several times; identical
    candidates make matching rightly ambiguous). False: every rendering, to test reading layouts."""
    rng = random.Random(seed)
    seen: set[tuple] = set()
    if out.exists():
        shutil.rmtree(out)
    (out / "invoices").mkdir(parents=True)
    (out / "labels").mkdir()
    invoices = []
    for pdf in sorted(corpus.rglob("*.pdf")):
        if "fail" in pdf.relative_to(corpus).parts or not (xml := embedded_xml(pdf)) or not (t := parse(xml)):
            continue
        key = (t["vendor"], t["invoice_number"], t["total"])
        if unique and key in seen:
            continue
        seen.add(key)
        name = f"r{len(invoices) + 1:03d}.pdf"
        shutil.copyfile(pdf, out / "invoices" / name)
        label = {"file": name, "source": str(pdf.relative_to(corpus)), "invoice": {
            **t, "line_items": []}}
        (out / "labels" / f"{Path(name).stem}.json").write_text(json.dumps(label, indent=2, ensure_ascii=False))
        invoices.append((name, t))

    wb = Workbook()
    ws = wb.active
    ws.title = "Sample"
    ws.append(["Row ID", "Vendor", "Invoice No", "Invoice Date", "Amount", "Currency"])
    expected = {}
    for i, (name, t) in enumerate(invoices, 1):
        rid, vendor, amount, d = f"R-{i:03d}", t["vendor"], Decimal(t["total"]), date.fromisoformat(t["invoice_date"])
        kind = rng.choices(["clean", "amount", "date", "missing"], [0.7, 0.12, 0.08, 0.1])[0]
        if kind == "amount":
            amount += Decimal(rng.choice(["1.00", "10.00", "-0.50", "100.00"]))
            expected[rid] = {"file": name, "expect": "difference", "field": "amount"}
        elif kind == "date":
            d += timedelta(days=rng.choice([-3, 1, 14]))
            expected[rid] = {"file": name, "expect": "difference", "field": "date"}
        elif kind == "missing":
            (out / "invoices" / name).unlink()  # the auditor asks for an invoice that was not provided
            expected[rid] = {"file": None, "expect": "no_evidence"}
        else:
            expected[rid] = {"file": name, "expect": "agrees"}
        ws.append([rid, vendor, t["invoice_number"], d, float(amount), t["currency"]])
    wb.save(out / "sample.xlsx")
    dates = sorted(t["invoice_date"] for _, t in invoices)
    rates = sorted({Decimal(r) / 100 for _, t in invoices for r in t["vat_rates"]})
    meta = {"source": "https://github.com/ZUGFeRD/corpus (Apache-2.0)", "invoices": len(invoices),
            "period": f"{dates[0]}:{dates[-1]}", "vat_rates": [str(r) for r in rates],
            "note": "expect = what row vs invoice should give; the invoice checks (VAT, totals, duplicates) "
                    "may add ⚠️/👀 on top, e.g. for invoices with several VAT rates.",
            "rows": expected}
    (out / "expected.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=Path("data/external/zugferd-corpus"))
    ap.add_argument("--out", type=Path, default=Path("data/real/facturx"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    meta = build(args.corpus, args.out, args.seed)
    build(args.corpus, args.out.with_name(args.out.name + "-all"), args.seed, unique=False)
    kinds = {}
    for r in meta["rows"].values():
        kinds[r["expect"]] = kinds.get(r["expect"], 0) + 1
    print(f"wrote {args.out}: {meta['invoices']} invoices, rows {kinds}, period {meta['period']}")
    print(f"wrote {args.out}-all: every rendering, for python -m tools.eval_reader {args.out}-all")


if __name__ == "__main__":
    main()
