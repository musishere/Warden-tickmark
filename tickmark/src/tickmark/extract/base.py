from pathlib import Path
from typing import Protocol

from tickmark.models import ExtractedInvoice

# Field names every extractor fills (when it can read them).
FIELDS = ("vendor", "invoice_number", "invoice_date", "total", "subtotal", "tax")


class Extractor(Protocol):
    def extract(self, pdf: Path) -> ExtractedInvoice: ...


def extract_all(extractor: Extractor, invoice_dir: Path) -> list[ExtractedInvoice]:
    return [extractor.extract(p) for p in sorted(Path(invoice_dir).glob("*.pdf"))]
