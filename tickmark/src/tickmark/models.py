from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class BBox(BaseModel):
    """Page coordinates in points, origin top-left."""

    x0: float
    y0: float
    x1: float
    y1: float


class Snip(BaseModel):
    """Where an extracted value came from. Every value links back to its source."""

    page: int
    bbox: BBox
    text: str


class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class Invoice(BaseModel):
    vendor: str
    invoice_number: str
    invoice_date: date
    currency: str = "EUR"
    line_items: list[LineItem]
    net_total: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    total: Decimal


class ExtractedField(BaseModel):
    name: str
    value: str
    confidence: float | None = None
    snip: Snip | None = None


class SampleRow(BaseModel):
    """One row of the auditor's sample: what the ledger says should be backed by an invoice."""

    row_id: int
    vendor: str
    amount: Decimal
    invoice_date: date
    reference: str | None = None
    excel_row: int | None = None  # source row in the workbook, for traceability


class InvoiceLabel(BaseModel):
    """Ground truth for one generated invoice (used by evals)."""

    file: str
    invoice: Invoice
    defects: list[str] = Field(default_factory=list)  # e.g. vat_wrong, lines_dont_sum
    contains_injection: bool = False


class SampleLabel(BaseModel):
    row_id: int
    expected: str  # matched | difference | no_evidence
    invoice_file: str | None = None
    difference_field: str | None = None


class RowError(BaseModel):
    row_number: int  # Excel row number, 1-based
    message: str


class SkippedRow(BaseModel):
    row_number: int
    reason: str


class SampleLoadResult(BaseModel):
    rows: list[SampleRow]
    errors: list[RowError]
    skipped: list[SkippedRow] = Field(default_factory=list)


class CleaningNote(BaseModel):
    row_id: int
    rule: str
    detail: str


class CleanRow(BaseModel):
    """A sample row as loaded, plus derived values. The original row is never modified."""

    row: SampleRow
    vendor_key: str
    flags: list[str] = Field(default_factory=list)


class CleanedSample(BaseModel):
    rows: list[CleanRow]
    notes: list[CleaningNote]
