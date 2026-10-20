"""A deliberately messy auditor sample, with the expected loader + cleaner outcome per Excel row."""
import json
from datetime import date
from pathlib import Path

from openpyxl import Workbook

# (excel cells, expected). Expected outcome: loaded | blank | skipped | error.
# Excel row numbers start at 2 (row 1 is the header).
MESSY_ROWS: list[tuple[list, dict]] = [
    ([1, "Northwind Logistics B.V.", 1200.00, date(2025, 3, 1)],
     {"outcome": "loaded", "vendor_key": "northwind logistics", "amount": "1200.0", "flags": []}),
    ([2, "NORTHWIND LOGISTICS BV", 980.50, date(2025, 4, 2)],
     {"outcome": "loaded", "vendor_key": "northwind logistics", "amount": "980.5", "flags": []}),
    ([3, "  contoso\xa0office   supplies ltd ", "1.234,50", "2025-05-10"],
     {"outcome": "loaded", "vendor_key": "contoso office supplies", "amount": "1234.50", "flags": []}),
    ([4, "Fabrikam Cloud Services GmbH", 625.57, date(2025, 8, 19)],
     {"outcome": "loaded", "vendor_key": "fabrikam cloud services", "amount": "625.57", "flags": []}),
    ([5, "fabrikam cloud services gmbh", 625.57, date(2025, 8, 19)],
     {"outcome": "loaded", "vendor_key": "fabrikam cloud services", "amount": "625.57",
      "flags": ["duplicate_of:4"]}),
    ([6, "Tailspin Catering S.A.", "(50.00)", "2025-06-01"],
     {"outcome": "loaded", "vendor_key": "tailspin catering", "amount": "-50.00",
      "flags": ["negative_amount"]}),
    ([7, "Proseware Print & Media", "€ 2,904.10", "11.10.2025"],
     {"outcome": "loaded", "vendor_key": "proseware print and media", "amount": "2904.10", "flags": []}),
    ([8, "Adatum Consulting Inc.", 0, date(2025, 7, 15)],
     {"outcome": "loaded", "vendor_key": "adatum consulting", "amount": "0", "flags": ["zero_amount"]}),
    ([None, None, None, None], {"outcome": "blank"}),
    ([9, "Litware Maintenance B.V.", 715.58, "03/04/2025"],
     {"outcome": "error", "reason": "ambiguous date"}),
    ([10, "Wingtip​ Travel Agency", "123.45-", date(2025, 2, 2)],
     {"outcome": "loaded", "vendor_key": "wingtip travel agency", "amount": "-123.45",
      "flags": ["negative_amount"]}),
    ([11, "Müller Büro GmbH", 300, date(2025, 9, 9)],
     {"outcome": "loaded", "vendor_key": "muller buro", "amount": "300", "flags": []}),
    ([12, "   ", 100, date(2025, 9, 10)], {"outcome": "error", "reason": "vendor is empty"}),
    ([13, "Contoso Office Supplies", "abc", date(2025, 9, 11)],
     {"outcome": "error", "reason": "amount is not a number"}),
    ([None, "Total", 8291.12, None], {"outcome": "skipped", "reason": "footer row"}),
]


def write_messy_sample(out: Path) -> int:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sample"
    ws.append(["Row", "Vendor", "Amount", "Date"])
    labels = []
    for excel_row, (cells, expected) in enumerate(MESSY_ROWS, start=2):
        ws.append(cells)
        labels.append({"excel_row": excel_row, **expected})
    wb.save(out / "sample_messy.xlsx")
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "labels" / "sample_messy_labels.json").write_text(json.dumps(labels, indent=2))
    return len(MESSY_ROWS)
