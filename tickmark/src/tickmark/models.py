from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    value: str  # normalised: dates ISO, amounts plain decimal ("2971.36")
    confidence: float | None = None
    snip: Snip | None = None


class ExtractedInvoice(BaseModel):
    """What an extractor read from one PDF. Field names: vendor, invoice_number, invoice_date,
    total, subtotal, tax. A field the extractor could not read is absent."""

    file: str
    fields: dict[str, ExtractedField] = Field(default_factory=dict)

    def get(self, name: str) -> ExtractedField | None:
        return self.fields.get(name)


class SampleRow(BaseModel):
    """One row of the auditor's sample: what the ledger says should be backed by an invoice."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    row_id: str  # as the auditor wrote it ("S-01", "7"); the Excel row number if the column is empty
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
    model_config = ConfigDict(coerce_numbers_to_str=True)

    row_id: str
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
    model_config = ConfigDict(coerce_numbers_to_str=True)

    row_id: str
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


class Candidate(BaseModel):
    """A PDF that might be the one a row is about, and which fields agree."""

    invoice_file: str
    agree: list[str]  # e.g. ["number", "vendor", "date"]
    note: str = ""  # why this candidate cannot be linked on its own, if it cannot


class PdfField(BaseModel):
    """One value read from a PDF, and whether matching may use it as evidence."""

    name: str
    value: str
    confidence: float | None = None
    trusted: bool
    why_not: str = ""
    snip: Snip | None = None


class FieldCheck(BaseModel):
    """One field of one row compared with one PDF."""

    field: str  # number | vendor | amount | date
    row_value: str
    pdf_value: str
    result: Literal["equal", "similar", "differs", "untrusted", "missing", "not in row"]
    detail: str = ""  # e.g. "vendor similarity 95", "+25.00", "+3 days", "confidence 0.5"


class Comparison(BaseModel):
    invoice_file: str
    checks: list[FieldCheck]
    outcome: str  # "can link", "review only: <why>", "not a candidate"


class Identification(BaseModel):
    """Which PDF a sample row is about. Only 'linked' names a PDF as the row's evidence."""

    row_id: str
    excel_row: int | None = None
    status: Literal["linked", "needs_review", "no_evidence"]
    invoice_file: str | None = None
    evidence: list[str] = Field(default_factory=list)  # human-readable, e.g. "number INV-1 equal"
    reason: str = ""  # for needs_review / no_evidence
    candidates: list[Candidate] = Field(default_factory=list)
    snips: list[Snip] = Field(default_factory=list)
    comparisons: list[Comparison] = Field(default_factory=list)  # this row vs every PDF
    steps: list[str] = Field(default_factory=list)  # the decision, in the order it was made
