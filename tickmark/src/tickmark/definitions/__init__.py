"""Audit test definitions: the meanings a sample column can have, their types and roles, and how
they are matched and checked against the evidence. Data, not code: a new audit type is a new definition.

A definition is a catalogue, not a schema: no column is required. A client layout maps whichever
columns it has (tickmark.fields.mapping) and may add meanings of its own (with_meanings); a run needs
only enough to identify the invoice (tickmark.fields.mapping.cannot_run).

Roles drive the generic engine (tickmark.match.identify, tickmark.vouch.compare):
  key           strongest identifier (invoice number): equal + one more field -> can link
  party         who the claim is with (vendor): equal + a corroborating field -> can link
  corroborating supports a link (amount, date)
  label         the auditor's own id for the row; never compared
  attribute     compared when vouching (currency), never used to link a row to a PDF
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

Role = Literal["key", "party", "corroborating", "label", "attribute"]


@dataclass(frozen=True)
class FieldSpec:
    name: str  # canonical name, the key in sample_rows.fields
    type: str  # one of tickmark.fields.types.TYPES
    role: Role
    label: str  # short word in traces and messages: "number", "vendor", "amount", "date"
    evidence_field: str | None = None  # the extracted document field it is compared with
    aliases: frozenset[str] = frozenset()  # header names that map without asking anyone
    description: str = ""  # for people and for the mapping proposal
    # an empty row value is unverifiable, never skipped: a claim that cannot be checked is not ✅
    compare_when_empty: bool = False
    # with no column of its own, read from "(XXX)" in the header of this field's column ("Amount (USD)")
    from_header_of: str | None = None
    # words printed next to the value on the document ("Invoice No", "Total"), for reading PDFs locally
    pdf_labels: frozenset[str] = frozenset()


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
    def compared(self) -> list[FieldSpec]:
        """Fields compared with the evidence, in trace order."""
        return [f for f in self.fields if f.evidence_field]

    @property
    def linking(self) -> list[FieldSpec]:
        """Compared fields that decide which PDF a row is about (attributes never do)."""
        return [f for f in self.compared if f.role != "attribute"]

    def with_meanings(self, meanings: dict) -> "Definition":
        """This catalogue plus a client layout's own meanings ({name: {type, labels, description}}):
        each is a claim compared with the document field of the same name, never used to link."""
        known = {f.name for f in self.fields}
        extra = tuple(FieldSpec(name, m["type"], "attribute", name.replace("_", " "), evidence_field=name,
                                pdf_labels=frozenset(label.casefold() for label in m["labels"]),
                                description=m.get("description", ""))
                      for name, m in meanings.items() if name not in known)
        return replace(self, fields=self.fields + extra) if extra else self

    def snapshot(self) -> dict:
        """Recorded with every run, so an old result can be explained after the definition changes."""
        d = asdict(self)
        for f in d["fields"]:
            f["aliases"], f["pdf_labels"] = sorted(f["aliases"]), sorted(f["pdf_labels"])
        d["sha256"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
        return d


INVOICE_VOUCHING = Definition(
    name="invoice_vouching",
    version=4,  # 2: currency compared; 3: no required column, more meanings, PDF labels; 4: amount due
    evidence="invoice",
    fields=(
        FieldSpec("row_id", "identifier", "label", "row id",
                  aliases=frozenset({"row", "row id", "id", "no", "#"}),
                  description="the auditor's own id for the sample row"),
        FieldSpec("reference", "identifier", "key", "number", evidence_field="invoice_number",
                  aliases=frozenset({"reference", "ref", "invoice no", "invoice number", "invoice #", "invoice id"}),
                  pdf_labels=frozenset({"invoice no", "invoice number", "invoice #", "invoice nr", "invoice id",
                                        "factuurnummer", "factuurnr", "rechnungsnummer", "rechnungsnr",
                                        "rechnung nr", "belegnummer", "facture n°", "numéro de facture",
                                        "n° de facture"}),
                  description="the invoice number"),
        FieldSpec("vendor", "name", "party", "vendor", evidence_field="vendor",
                  aliases=frozenset({"vendor", "supplier", "vendor name", "supplier name"}),
                  pdf_labels=frozenset({"supplier", "vendor", "seller", "leverancier", "lieferant", "verkäufer",
                                        "fournisseur", "vendeur"}),
                  description="the supplier who issued the invoice"),
        FieldSpec("customer", "name", "attribute", "customer", evidence_field="customer",
                  aliases=frozenset({"customer", "customer name", "client", "bill to"}),
                  pdf_labels=frozenset({"bill to", "invoice to", "sold to", "customer", "klant",
                                        "rechnungsempfänger", "kunde", "client", "destinataire"}),
                  description="who the invoice is addressed to"),
        FieldSpec("amount", "money", "corroborating", "amount", evidence_field="total",
                  aliases=frozenset({"amount", "total", "invoice amount", "invoice total", "gross amount"}),
                  pdf_labels=frozenset({"total", "invoice total", "grand total", "total incl vat", "totaal",
                                        "gesamtbetrag", "bruttosumme", "rechnungsbetrag", "endbetrag", "gesamtsumme",
                                        "total ttc", "montant ttc"}),
                  description="the invoice total including tax"),
        FieldSpec("amount_due", "money", "attribute", "amount due", evidence_field="amount_due",
                  pdf_labels=frozenset({"amount due", "total due", "balance due", "te betalen", "zahlbetrag",
                                        "zu zahlen", "net à payer", "solde à payer"}),
                  description="what is left to pay: the total less prepayments, plus arrears"),
        FieldSpec("charges", "money", "attribute", "charges", evidence_field="charges",
                  pdf_labels=frozenset({"gesamtbetrag der zuschläge", "total charges"}),
                  description="the invoice's surcharges (shipping, packaging): not its total"),
        FieldSpec("allowances", "money", "attribute", "allowances", evidence_field="allowances",
                  pdf_labels=frozenset({"gesamtbetrag der abschläge", "total allowances", "total discounts"}),
                  description="the invoice's discounts: not its total"),
        FieldSpec("net_amount", "money", "attribute", "net amount", evidence_field="subtotal",
                  aliases=frozenset({"net", "net amount", "subtotal", "amount excl vat", "net total"}),
                  pdf_labels=frozenset({"net total", "subtotal", "sub total", "net amount", "total excl vat",
                                        "totaal excl btw", "nettobetrag", "nettosumme", "summe netto",
                                        "rechnungssumme ohne ust", "zwischensumme", "total ht", "montant ht"}),
                  description="the invoice amount before tax"),
        FieldSpec("vat_amount", "money", "attribute", "VAT", evidence_field="tax",
                  aliases=frozenset({"vat", "tax", "vat amount", "tax amount", "btw"}),
                  pdf_labels=frozenset({"vat", "tax", "vat amount", "total vat", "btw", "mwst", "ust",
                                        "umsatzsteuer", "mehrwertsteuer", "steuerbetrag", "tva", "montant tva",
                                        "total tva", "total taxes", "tax total", "total tax"}),
                  description="the tax (VAT) on the invoice"),
        FieldSpec("invoice_date", "date", "corroborating", "date", evidence_field="invoice_date",
                  aliases=frozenset({"date", "invoice date"}),
                  pdf_labels=frozenset({"date", "invoice date", "factuurdatum", "datum", "rechnungsdatum",
                                        "belegdatum", "date de facture", "date facture"}),
                  description="the invoice date"),
        FieldSpec("due_date", "date", "attribute", "due date", evidence_field="due_date",
                  aliases=frozenset({"due date", "due"}),
                  pdf_labels=frozenset({"due date", "payment due", "vervaldatum", "fällig am", "zahlbar bis",
                                        "fälligkeitsdatum", "date d'échéance", "échéance"}),
                  description="when payment is due"),
        FieldSpec("po_number", "identifier", "attribute", "PO", evidence_field="po_number",
                  aliases=frozenset({"po", "po number", "purchase order", "order number"}),
                  pdf_labels=frozenset({"po", "po number", "purchase order", "order number", "your order",
                                        "bestellnummer", "ihre bestellung", "bon de commande"}),
                  description="the buyer's purchase order number"),
        FieldSpec("currency", "currency", "attribute", "currency", evidence_field="currency",
                  aliases=frozenset({"currency", "ccy", "cur", "currency code"}),
                  pdf_labels=frozenset({"currency", "invoice currency", "rechnungswährung", "devise", "valuta"}),
                  # not a bare 'Währung': 'Währung der Umsatzsteuerbuchung' is the VAT's currency, not the invoice's
                  description="the currency of the amount (three-letter code)",
                  compare_when_empty=True, from_header_of="amount"),
    ),
    checks=("totals_reconcile", "lines_sum", "vat_rate", "in_period", "duplicate"),
    reconcile={"total": ("subtotal", "tax")},
)

DEFINITIONS = {INVOICE_VOUCHING.name: INVOICE_VOUCHING}
