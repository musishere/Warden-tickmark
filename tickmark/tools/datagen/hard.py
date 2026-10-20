"""Hard negatives for identification: cases where a careless matcher links the wrong PDF.

    python -m tools.datagen.hard [--out data/generated/hard]

Writes invoices/*.pdf, labels/*.json (InvoiceLabel), sample.xlsx and truth.json. Each truth row
says which PDF the row is really about (or null) and the safe outcome: link | review | none.
"""
import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from tickmark.models import Invoice, InvoiceLabel, LineItem
from tools.datagen.generate import VAT_RATE, money, render_pdf

NW, CO, FA = "Northwind Logistics B.V.", "Contoso Office Supplies Ltd", "Fabrikam Cloud Services GmbH"
TA, AD, LI = "Tailspin Catering S.A.", "Adatum Consulting Inc.", "Litware Maintenance B.V."
WI, PR = "Wingtip Travel Agency", "Proseware Print & Media"

# file stem -> (vendor, invoice number, date, net)
PDFS = {
    "hard_01a": (NW, "N-101", date(2025, 2, 1), 100), "hard_01b": (NW, "N-102", date(2025, 2, 15), 100),
    "hard_02a": (CO, "C-201", date(2025, 3, 3), 200), "hard_02b": (CO, "C-202", date(2025, 3, 3), 300),
    "hard_03a": (FA, "F-301", date(2025, 4, 1), 400), "hard_03b": (FA, "F-302", date(2025, 4, 20), 400),
    "hard_04": (TA, "T-401", date(2025, 5, 5), 500),
    "hard_05a": (AD, "R-500", date(2025, 6, 1), 600), "hard_05b": (LI, "R-500", date(2025, 6, 2), 700),
    "hard_06": (WI, "W-600", date(2025, 7, 7), 650),
    "hard_07": (PR, "P-700", date(2025, 8, 8), 800),
    "hard_08": (LI, "L-800", date(2025, 9, 9), 900),
    "hard_09": (AD, "A-900", date(2025, 10, 10), 1000),
    "hard_10": (CO, "C-1000", date(2025, 11, 11), 1100),
}

# (row id, vendor, amount, date, reference, truth pdf stem or None, safe outcome, what it tests)
ROWS = [
    ("H-01", NW, "121.00", date(2025, 2, 15), None, "hard_01b", "link", "same vendor+amount, date decides"),
    ("H-02", CO, "363.00", date(2025, 3, 3), None, "hard_02b", "link", "same vendor+date, amount decides"),
    ("H-03", FA, "484.00", date(2025, 4, 10), None, None, "review", "two PDFs equally plausible"),
    ("H-04", TA, "605.00", date(2025, 5, 5), "T-4O1", "hard_04", "review", "typo'd invoice number"),
    ("H-05", LI, "847.00", date(2025, 6, 2), "R-500", "hard_05b", "link", "number reused by two vendors"),
    ("H-06", "Unknown Holdings", "1.00", date(2025, 1, 1), "W-600", None, "review", "number alone"),
    ("H-07", "PROSEWARE PRINT AND MEDIA", "968.00", date(2025, 8, 8), None, "hard_07", "link",
     "vendor spelling variant"),
    ("H-08", "Litware Maintenence B.V.", "1089.00", date(2025, 9, 9), None, None, "review",
     "lookalike vendor"),
    ("H-09", "Initech Unlisted Supplies", "1210.00", date(2025, 10, 10), None, None, "none",
     "decoy vendor with the same amount and date"),
    ("H-10", CO, "1331.00", date(2025, 11, 11), None, None, "review", "duplicate row (1/2)"),
    ("H-11", CO, "1331.00", date(2025, 11, 11), None, None, "review", "duplicate row (2/2)"),
]


def make(vendor: str, number: str, d: date, net: int) -> Invoice:
    net_d = money(Decimal(net))
    vat = money(net_d * VAT_RATE)
    item = LineItem(description="Services", quantity=Decimal(1), unit_price=net_d, amount=net_d)
    return Invoice(vendor=vendor, invoice_number=number, invoice_date=d, line_items=[item],
                   net_total=net_d, vat_rate=VAT_RATE, vat_amount=vat, total=net_d + vat)


def write_hard_set(out: Path) -> int:
    (out / "invoices").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    for stem, spec in PDFS.items():
        inv = make(*spec)
        render_pdf(inv, out / "invoices" / f"{stem}.pdf", injection=False)
        label = InvoiceLabel(file=f"{stem}.pdf", invoice=inv)
        (out / "labels" / f"{stem}.json").write_text(label.model_dump_json(indent=2))
    wb = Workbook()
    ws = wb.active
    ws.title = "Sample"
    ws.append(["Row ID", "Vendor", "Invoice No", "Invoice Date", "Amount"])
    truth = []
    for row_id, vendor, amount, d, ref, stem, expect, what in ROWS:
        ws.append([row_id, vendor, ref, d, float(amount)])
        truth.append({"row_id": row_id, "invoice_file": f"{stem}.pdf" if stem else None,
                      "expect": expect, "tests": what})
    wb.save(out / "sample.xlsx")
    (out / "truth.json").write_text(json.dumps(truth, indent=2))
    return len(ROWS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/generated/hard"))
    args = ap.parse_args()
    n = write_hard_set(args.out)
    print(f"wrote {n} hard rows and {len(PDFS)} invoices to {args.out}/")


if __name__ == "__main__":
    main()
