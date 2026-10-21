from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tickmark.definitions import INVOICE_VOUCHING
from tickmark.fields.types import TYPES


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


class ExtractedLineItem(BaseModel):
    """One invoice line as read. Values normalised like ExtractedField; None if not read."""

    description: str = ""
    quantity: str | None = None
    unit_price: str | None = None
    amount: str | None = None
    confidence: float | None = None  # the lowest confidence of the line's values
    snip: Snip | None = None


class TextLine(BaseModel):
    """A run of words on one line of a page, with where it is (points, origin top-left)."""

    page: int
    text: str
    bbox: BBox


class ExtractedInvoice(BaseModel):
    """What an extractor read from one PDF. Field names: vendor, invoice_number, invoice_date,
    total, subtotal, tax, currency, and any other meaning's evidence field. A field the extractor
    could not read is absent; `unread` says why when it knows (e.g. two different totals)."""

    file: str
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    extractor: str = ""  # local | azure-docint | labels
    model_id: str = ""
    api_version: str | None = None
    raw: dict | None = Field(default=None, exclude=True)  # what the extractor returned, for the audit store
    needs_ocr: list[str] = Field(default_factory=list)  # why the file cannot be read from its own text
    unread: dict[str, str] = Field(default_factory=dict)  # field -> why it was not read
    lines: list[TextLine] = Field(default_factory=list, exclude=True)  # the text, to find a value without a label
    letterhead: int = Field(default=0, exclude=True)  # lines[:letterhead] are the issuer's letterhead

    def get(self, name: str) -> ExtractedField | None:
        return self.fields.get(name)


class Cell(BaseModel):
    """One Excel cell exactly as received. A row keeps a LIST of these, so duplicate and
    reordered headers survive."""

    col: str  # column letter, e.g. "C"
    header: str | None = None  # the header text above it, as written
    value: Any = None  # JSON-safe: text, number, bool, ISO date string, or None
    kind: str = "empty"  # the Excel value's Python type: str, int, float, date, datetime, bool, empty


class FieldValue(BaseModel):
    """A mapped, typed value: what the rules read."""

    type: str  # a tickmark.fields.types name
    value: str  # canonical string ("1234.50", "2026-01-13"); money is never a JSON number
    col: str | None = None  # the column it came from


_LEGACY = ("reference", "vendor", "amount", "invoice_date")


class SampleRow(BaseModel):
    """One row of the auditor's sample: what the ledger says should be backed by evidence.

    `fields` holds the typed values its audit definition needs; `raw` the row's cells as received.
    Constructing it with vendor= / amount= / invoice_date= / reference= (the invoice_vouching
    fields) still works, and those names read back as properties."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    row_id: str  # as the auditor wrote it ("S-01", "7"); the Excel row number if the column is empty
    excel_row: int | None = None  # source row in the workbook, for traceability
    definition: str = INVOICE_VOUCHING.name
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    raw: list[Cell] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_kwargs(cls, data):
        if isinstance(data, dict) and any(k in data for k in _LEGACY):
            data = dict(data)
            fields = dict(data.get("fields") or {})
            for name in _LEGACY:
                v = data.pop(name, None)
                if v is not None:
                    t = TYPES[INVOICE_VOUCHING.field(name).type]
                    typed = t.parse(v, name) if isinstance(v, str) else v  # "10.50" -> Decimal, "2025-03-01" -> date
                    fields[name] = FieldValue(type=t.name, value=t.to_str(typed))
            data["fields"] = fields
        return data

    def get(self, name: str):
        """The typed Python value of a field (Decimal, date, str), or None if absent."""
        f = self.fields.get(name)
        return TYPES[f.type].from_str(f.value) if f else None

    def set(self, name: str, value) -> None:
        """Replace a field's value (tests use it to tamper with a loaded row)."""
        t = TYPES[INVOICE_VOUCHING.field(name).type] if name not in self.fields else TYPES[self.fields[name].type]
        col = self.fields[name].col if name in self.fields else None
        self.fields[name] = FieldValue(type=t.name, value=t.to_str(value), col=col)

    vendor = property(lambda self: self.get("vendor"), lambda self, v: self.set("vendor", v))
    amount = property(lambda self: self.get("amount"), lambda self, v: self.set("amount", v))
    invoice_date = property(lambda self: self.get("invoice_date"), lambda self, v: self.set("invoice_date", v))
    reference = property(lambda self: self.get("reference"), lambda self, v: self.set("reference", v))


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
    raw: list[Cell] = Field(default_factory=list)


class SkippedRow(BaseModel):
    row_number: int
    reason: str
    raw: list[Cell] = Field(default_factory=list)


class SampleLoadResult(BaseModel):
    rows: list[SampleRow]
    errors: list[RowError]
    skipped: list[SkippedRow] = Field(default_factory=list)
    sheet: str | None = None  # the sheet's title
    headers: list[Cell] = Field(default_factory=list)  # the header row, one Cell per column
    mapping: Any = None  # the ColumnMapping that turned raw cells into fields
    definition: Any = None  # the Definition the rows were read with (catalogue + the layout's own meanings)


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


class FieldAgreement(BaseModel):
    """One claimed field of a row compared with the linked PDF."""

    field: str  # number | vendor | amount | date
    row_value: str
    pdf_value: str
    result: Literal["agrees", "differs", "unverifiable"]
    delta: str = ""  # e.g. "+250.00 (row − PDF)", "+3 days (row − PDF)"
    detail: str = ""


class CheckResult(BaseModel):
    """One check of the invoice on its own (totals, lines, VAT, period, duplicates)."""

    name: str
    status: Literal["pass", "fail", "warn", "not_checkable"]
    detail: str
    snips: list[Snip] = Field(default_factory=list)


class Verdict(BaseModel):
    """Does the PDF back the row's claim? matched ✅ / difference ⚠️ / needs_review 👀 / no_evidence ❓."""

    row_id: str
    excel_row: int | None = None
    verdict: Literal["matched", "difference", "needs_review", "no_evidence"]
    invoice_file: str | None = None
    agreements: list[FieldAgreement] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)  # why it is not ✅, one line per issue
    steps: list[str] = Field(default_factory=list)
