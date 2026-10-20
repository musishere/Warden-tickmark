"""Audit test definitions: which fields a test needs, their types and roles, and how they are
matched and checked against the evidence. Data, not code: a new audit type is a new definition.

Roles drive the generic engine (tickmark.match.identify, tickmark.vouch.compare):
  key           strongest identifier (invoice number): equal + one more field -> can link
  party         who the claim is with (vendor): equal + a corroborating field -> can link
  corroborating supports a link (amount, date)
  label         the auditor's own id for the row; never compared
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Literal

Role = Literal["key", "party", "corroborating", "label"]


@dataclass(frozen=True)
class FieldSpec:
    name: str  # canonical name, the key in sample_rows.fields
    type: str  # one of tickmark.fields.types.TYPES
    role: Role
    label: str  # short word in traces and messages: "number", "vendor", "amount", "date"
    required: bool = False
    evidence_field: str | None = None  # the extracted document field it is compared with
    aliases: frozenset[str] = frozenset()  # header names that map without asking anyone
    description: str = ""  # for people and for the mapping proposal


@dataclass(frozen=True)
class Definition:
    name: str
    version: int
    evidence: str  # the document type that backs a row
    fields: tuple[FieldSpec, ...]
    checks: tuple[str, ...]
    # extracted fields that only count as evidence when they reconcile, e.g. total = subtotal + tax
    reconcile: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)

    def by_role(self, role: Role) -> list[FieldSpec]:
        return [f for f in self.fields if f.role == role]

    @property
    def required(self) -> list[FieldSpec]:
        return [f for f in self.fields if f.required]

    @property
    def compared(self) -> list[FieldSpec]:
        """Fields compared with the evidence, in trace order."""
        return [f for f in self.fields if f.evidence_field]

    def snapshot(self) -> dict:
        """Recorded with every run, so an old result can be explained after the definition changes."""
        d = asdict(self)
        for f in d["fields"]:
            f["aliases"] = sorted(f["aliases"])
        d["sha256"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
        return d


INVOICE_VOUCHING = Definition(
    name="invoice_vouching",
    version=1,
    evidence="invoice",
    fields=(
        FieldSpec("row_id", "identifier", "label", "row id",
                  aliases=frozenset({"row", "row id", "id", "no", "#"}),
                  description="the auditor's own id for the sample row"),
        FieldSpec("reference", "identifier", "key", "number", evidence_field="invoice_number",
                  aliases=frozenset({"reference", "ref", "invoice no", "invoice number", "invoice #", "invoice id"}),
                  description="the invoice number"),
        FieldSpec("vendor", "name", "party", "vendor", required=True, evidence_field="vendor",
                  aliases=frozenset({"vendor", "supplier", "vendor name", "supplier name"}),
                  description="the supplier who issued the invoice"),
        FieldSpec("amount", "money", "corroborating", "amount", required=True, evidence_field="total",
                  aliases=frozenset({"amount", "total", "invoice amount", "invoice total"}),
                  description="the invoice total including tax"),
        FieldSpec("invoice_date", "date", "corroborating", "date", required=True, evidence_field="invoice_date",
                  aliases=frozenset({"date", "invoice date"}),
                  description="the invoice date"),
    ),
    checks=("totals_reconcile", "lines_sum", "vat_rate", "in_period", "duplicate"),
    reconcile={"total": ("subtotal", "tax")},
)

DEFINITIONS = {INVOICE_VOUCHING.name: INVOICE_VOUCHING}
