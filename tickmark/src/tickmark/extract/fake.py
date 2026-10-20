"""A stand-in extractor that reads the ground-truth label JSON next to each PDF.

It is "perfect extraction": use it to test and measure matching on its own, never as evidence.
Understands both label formats in data/: generated (InvoiceLabel) and the external sample pack.
"""
import json
from pathlib import Path

from tickmark.models import ExtractedField, ExtractedInvoice, ExtractedLineItem


class LabelExtractor:
    def __init__(self, labels_dir: Path):
        self.labels_dir = Path(labels_dir)

    def extract(self, pdf: Path) -> ExtractedInvoice:
        pdf = Path(pdf)
        label = json.loads((self.labels_dir / f"{pdf.stem}.json").read_text())
        if "invoice" in label:  # generated: InvoiceLabel
            inv = label["invoice"]
            values = {"vendor": inv["vendor"], "invoice_number": inv["invoice_number"],
                      "invoice_date": inv["invoice_date"], "total": inv["total"],
                      "subtotal": inv["net_total"], "tax": inv["vat_amount"]}
            lines = inv["line_items"]
        else:  # external pack: what is printed wins over what should have been printed
            values = {"vendor": label["vendor_name"], "invoice_number": label["invoice_number"],
                      "invoice_date": label["invoice_date"],
                      "total": label.get("printed_total", label["total"]),
                      "subtotal": label["subtotal"], "tax": label["vat_amount"]}
            lines = label["line_items"]
        return ExtractedInvoice(
            file=pdf.name, extractor="labels", model_id="ground-truth", raw=label,
            fields={k: ExtractedField(name=k, value=str(v), confidence=1.0) for k, v in values.items()},
            line_items=[ExtractedLineItem(description=li["description"], quantity=str(li["quantity"]),
                                          unit_price=str(li["unit_price"]), amount=str(li["amount"]),
                                          confidence=1.0) for li in lines])
