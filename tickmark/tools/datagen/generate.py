"""Generate synthetic invoices (PDF), labels (JSON) and the auditor's sample (xlsx).

    python -m tools.datagen.generate --count 20 --seed 1 [--messy]
"""
import argparse
import json
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from tickmark.models import Invoice, InvoiceLabel, LineItem, SampleLabel, SampleRow
from tools.datagen.messy import write_messy_sample
from tools.datagen.vendors import INJECTION_TEXT, LINE_ITEMS, VENDORS

PERIOD = (date(2025, 1, 1), date(2025, 12, 31))
VAT_RATE = Decimal("0.21")
CENT = Decimal("0.01")

DEFECTS = ["vat_wrong", "lines_dont_sum", "date_out_of_period", "duplicate_number"]


def money(x: Decimal | float) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def make_invoice(rng: random.Random, number: int) -> Invoice:
    vendor, _ = rng.choice(VENDORS)
    items = []
    for desc, lo, hi in rng.sample(LINE_ITEMS, rng.randint(1, 4)):
        qty = Decimal(rng.randint(1, 10))
        unit = money(rng.uniform(lo, hi) / float(qty))
        items.append(LineItem(description=desc, quantity=qty, unit_price=unit, amount=money(qty * unit)))
    net = sum((i.amount for i in items), Decimal(0))
    vat = money(net * VAT_RATE)
    d = PERIOD[0] + timedelta(days=rng.randint(0, (PERIOD[1] - PERIOD[0]).days))
    return Invoice(
        vendor=vendor,
        invoice_number=f"INV-{2025}-{number:04d}",
        invoice_date=d,
        line_items=items,
        net_total=net,
        vat_rate=VAT_RATE,
        vat_amount=vat,
        total=net + vat,
    )


def apply_defect(inv: Invoice, defect: str, earlier_number: str | None) -> Invoice:
    """Return the invoice AS PRINTED (the defect is what the checks must catch)."""
    data = inv.model_copy(deep=True)
    if defect == "vat_wrong":
        data.vat_amount = money(data.vat_amount + Decimal("12.50"))
        data.total = data.net_total + data.vat_amount
    elif defect == "lines_dont_sum":
        data.net_total = data.net_total + Decimal("40.00")
        data.vat_amount = money(data.net_total * data.vat_rate)
        data.total = data.net_total + data.vat_amount
    elif defect == "date_out_of_period":
        data.invoice_date = date(2024, 12, 15)
    elif defect == "duplicate_number" and earlier_number:
        data.invoice_number = earlier_number
    return data


def render_pdf(inv: Invoice, path: Path, injection: bool) -> None:
    address = dict(VENDORS).get(inv.vendor, "")
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, h - 60, inv.vendor)
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 76, address)
    c.drawString(400, h - 60, f"Invoice No: {inv.invoice_number}")
    c.drawString(400, h - 76, f"Date: {inv.invoice_date.isoformat()}")
    y = h - 130
    c.setFont("Helvetica-Bold", 10)
    for x, t in ((50, "Description"), (300, "Qty"), (360, "Unit price"), (460, "Amount")):
        c.drawString(x, y, t)
    c.setFont("Helvetica", 10)
    for li in inv.line_items:
        y -= 18
        c.drawString(50, y, li.description)
        c.drawString(300, y, str(li.quantity))
        c.drawString(360, y, f"{li.unit_price:.2f}")
        c.drawString(460, y, f"{li.amount:.2f}")
    y -= 30
    for label, val in (
        ("Net total", inv.net_total),
        (f"VAT {inv.vat_rate * 100:.0f}%", inv.vat_amount),
        (f"Total ({inv.currency})", inv.total),
    ):
        c.drawString(360, y, label)
        c.drawString(460, y, f"{val:.2f}")
        y -= 16
    if injection:
        c.setFont("Helvetica", 6)
        c.setFillGray(0.6)  # faint footer text, as a hostile vendor might hide it
        c.drawString(50, 40, INJECTION_TEXT)
    c.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("data/generated"))
    ap.add_argument("--messy", action="store_true", help="also write sample_messy.xlsx")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    inv_dir, label_dir = args.out / "invoices", args.out / "labels"
    inv_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    injection_idx = args.count - 1
    labels: list[InvoiceLabel] = []
    for n in range(1, args.count + 1):
        clean = make_invoice(rng, n)
        defect = rng.choice(DEFECTS) if rng.random() < 0.3 else None
        earlier = labels[0].invoice.invoice_number if labels else None
        printed = apply_defect(clean, defect, earlier) if defect else clean
        injection = n - 1 == injection_idx
        fname = f"invoice_{n:03d}.pdf"
        render_pdf(printed, inv_dir / fname, injection)
        label = InvoiceLabel(
            file=fname, invoice=printed, defects=[defect] if defect else [], contains_injection=injection
        )
        labels.append(label)
        (label_dir / f"invoice_{n:03d}.json").write_text(label.model_dump_json(indent=2))

    # Auditor's sample: most rows have evidence, some differ, some have none.
    rows, row_labels = [], []
    for i, lab in enumerate(labels[: max(1, int(args.count * 0.8))], start=1):
        inv = lab.invoice
        kind = rng.choices(["matched", "difference", "no_evidence"], [0.6, 0.25, 0.15])[0]
        amount = inv.total
        if kind == "difference":
            amount = money(amount + Decimal(rng.choice([5, 25, 100])))
        vendor = inv.vendor
        if kind == "no_evidence":
            vendor, amount = "Initech Unlisted Supplies", money(rng.uniform(200, 5000))
        rows.append(SampleRow(row_id=i, vendor=vendor, amount=amount, invoice_date=inv.invoice_date))
        row_labels.append(
            SampleLabel(
                row_id=i,
                expected=kind,
                invoice_file=None if kind == "no_evidence" else lab.file,
                difference_field="amount" if kind == "difference" else None,
            )
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "Sample"
    ws.append(["Row", "Vendor", "Amount", "Date"])
    for r in rows:
        ws.append([r.row_id, r.vendor, float(r.amount), r.invoice_date])
    wb.save(args.out / "sample.xlsx")
    (label_dir / "sample_labels.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in row_labels], indent=2)
    )
    print(f"wrote {len(labels)} invoices and {len(rows)} sample rows to {args.out}/")
    if args.messy:
        n = write_messy_sample(args.out)
        print(f"wrote sample_messy.xlsx with {n} data rows to {args.out}/")


if __name__ == "__main__":
    main()
