"""Which reader reads each PDF: its own text first (free), Azure only where that is not reliable.

settings.azure (or the tools' --azure):
  auto    1. every PDF is read locally; one that needs OCR (scan, broken text, rotated) goes to Azure
          2. after matching and vouching, PDFs where a value the row needs was not read clearly go
             to Azure (second_pass); the run is then matched and vouched again, once
  off     never Azure: what local reading cannot settle stays 👀 / ❓, with the reason
  always  every PDF goes to Azure (as before); local reading still fills meanings Azure has no field for

A PDF read by both is merged: Azure's fields, local values where Azure has none, and a field the two
read differently is not trusted at all (not read, with both values as the reason).
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tickmark.config import settings
from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.extract.azure_docint import FIELD_MAP, DocIntError, DocIntExtractor, ocr_words
from tickmark.extract.local import LocalExtractor
from tickmark.extract.text import corroborate, found_in_text, normalize_number
from tickmark.log import get
from tickmark.models import ExtractedInvoice

log = get("router")
AZURE_FIELDS = {name for name, _ in FIELD_MAP.values()} | {"currency"}  # what Azure's invoice model reads itself

# what each invoice check needs read from the PDF (lines_sum: the item table, which only Azure reads)
CHECK_NEEDS = {"totals_reconcile": ("subtotal", "tax", "total"), "vat_rate": ("subtotal", "tax"),
               "in_period": ("invoice_date",), "duplicate": ("invoice_number", "vendor"), "lines_sum": ()}


def _same(a: str, b: str) -> bool:
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return a.strip().casefold() == b.strip().casefold()


class Router:
    def __init__(self, mode: str | None = None, azure=None, definition: Definition = INVOICE_VOUCHING):
        self.mode = mode or settings.azure
        if self.mode not in ("auto", "off", "always"):
            raise ValueError(f"azure must be auto, off or always, not {self.mode!r}")
        self.azure = azure or DocIntExtractor()
        self.definition = definition
        self.routes: dict[str, tuple[str, str]] = {}  # file -> (read_by, why Azure)

    def use(self, definition: Definition) -> None:
        """The run's definition: which meanings to look for (set by the pipeline before reading)."""
        self.definition = definition

    def extract(self, pdf: Path) -> ExtractedInvoice:
        local = LocalExtractor(self.definition).extract(pdf)
        if self.mode == "always":
            return self._azure(pdf, local, "every PDF goes to Azure (TICKMARK_AZURE=always)")
        if local.needs_ocr and self.mode == "auto":
            return self._azure(pdf, local, "; ".join(local.needs_ocr))
        if local.needs_ocr:
            log.warning("%s: needs OCR but Azure is off: left unread", local.file)
        else:
            log.info("%s: kept the local reading (Azure %s)", local.file, self.mode)
        self.routes[local.file] = ("local", "; ".join(local.needs_ocr))
        return local

    def reread(self, pdf: Path, local: ExtractedInvoice, why: str) -> ExtractedInvoice:
        return self._azure(pdf, local, why)

    def _azure(self, pdf: Path, local: ExtractedInvoice, why: str) -> ExtractedInvoice:
        log.info("%s → Azure: %s", local.file, why)
        try:
            inv = self.azure.extract(pdf)
        except DocIntError as e:  # not configured or failed: keep the local reading, say what was missing
            log.warning("%s: Azure not available (%s): keeping the local reading", local.file, e)
            self.routes[local.file] = ("local", f"Azure needed ({why}) but not available: {e}")
            if local.needs_ocr:
                local.needs_ocr = [*local.needs_ocr, "Azure not available"]
            return local
        # Azure's own fields win. Label reading (on the PDF's text, or on Azure's OCR words for a scan)
        # only fills meanings Azure has no field for: measured on real invoices, it misreads far more.
        from_ocr = LocalExtractor(self.definition).from_words(ExtractedInvoice(file=inv.file), ocr_words(inv.raw)) \
            if inv.raw else ExtractedInvoice(file=inv.file)
        readers = [from_ocr] if local.needs_ocr else [local, from_ocr]
        for other in readers:
            for name, f in other.fields.items():
                if name not in AZURE_FIELDS:
                    inv.fields.setdefault(name, f)
                elif name in inv.fields and other is local:
                    if _same(inv.fields[name].value, f.value):  # two readers agree: that confirms it
                        inv.fields[name] = inv.fields[name].model_copy(update={"corroborated": "local reading agrees"})
                    else:
                        log.info("%s: %s: using Azure's %r, not the local %r", inv.file, name,
                                 inv.fields[name].value, f.value)
        # the PDF's own text layer is independent of Azure; on a scan only Azure's OCR lines exist
        inv.lines = local.lines if not local.needs_ocr and local.lines else from_ocr.lines
        inv.letterhead = local.letterhead if inv.lines is local.lines else from_ocr.letterhead
        corroborate(inv)
        self.routes[inv.file] = ("azure", why)
        return inv

    def second_pass(self, run) -> dict[str, str]:
        """PDFs read locally that Azure should read: file -> why. Only in auto mode."""
        if self.mode != "auto":
            return {}
        by_file = {inv.file: inv for inv in run.extracted}
        local = {f for f, (by, _) in self.routes.items() if by == "local" and not by_file[f].needs_ocr}
        rows = {cr.row.excel_row: cr.row for cr in run.cleaned.rows}
        defaults = {"currency": run.policy.currency} if run.policy else {}
        want: dict[str, str] = {}

        def missing(inv: ExtractedInvoice, names) -> list[str]:
            return [n for n in names if inv.get(n) is None]

        def unread_linking(inv: ExtractedInvoice) -> list[str]:
            """Linking values not read; a name or number printed without a label can still be found
            in the text (found_in_text), so with text those do not count."""
            return [s.evidence_field for s in self.definition.linking if inv.get(s.evidence_field) is None
                    and not (inv.lines and s.type in ("name", "identifier"))]

        for v in run.verdicts:  # linked rows: a value the row needs that was not read clearly
            if v.invoice_file not in local:
                continue
            inv, row = by_file[v.invoice_file], rows[v.excel_row]
            needs = [s.evidence_field for s in self.definition.compared
                     if (row.get(s.name) is not None or defaults.get(s.name)) and inv.get(s.evidence_field) is None
                     and not found_in_text(inv, s.type, s.role, row.get(s.name))]
            for c in v.checks:
                if c.status == "not_checkable" and c.name == "lines_sum":
                    needs.append("line items")
                elif c.status == "not_checkable":
                    needs += missing(inv, CHECK_NEEDS.get(c.name, ()))
                if c.status == "not_checkable" and c.name == "duplicate" and (number := inv.get("invoice_number")):
                    for other in local - {inv.file}:  # the other PDFs with this number need their vendor too
                        o = by_file[other].get("invoice_number")
                        if o and normalize_number(o.value) == normalize_number(number.value) \
                                and by_file[other].get("vendor") is None:
                            want.setdefault(other, f"shares invoice number {number.value} with {inv.file}: vendor needed")
            if needs:
                want[v.invoice_file] = "not read clearly: " + ", ".join(dict.fromkeys(needs))
        for r in run.results:  # several candidates, one of them not read completely
            if r.status == "needs_review":
                for c in r.candidates:
                    if c.invoice_file in local and (m := unread_linking(by_file[c.invoice_file])):
                        want.setdefault(c.invoice_file, "candidate not read completely: " + ", ".join(m))
        if any(r.status == "no_evidence" for r in run.results):  # an unlinked PDF that may be the missing one
            linked = {r.invoice_file for r in run.results if r.status == "linked"}
            mapped = {s.evidence_field for s in self.definition.linking
                      if any(row.get(s.name) is not None for row in rows.values())}
            for f in sorted(local - linked):
                if m := [n for n in unread_linking(by_file[f]) if n in mapped]:
                    want.setdefault(f, "a row has no PDF, and this one was not read completely: " + ", ".join(m))
        log.info("second pass: %s", f"{len(want)} PDF(s) to Azure" if want else "nothing needs Azure")
        return want

    def summary(self) -> str:
        n_local = sum(by == "local" for by, _ in self.routes.values())
        return f"{len(self.routes)} PDFs: {n_local} read locally, {len(self.routes) - n_local} by Azure"
