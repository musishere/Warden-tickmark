"""Read the auditor's Excel sample into SampleRow objects.

read_sheet (no schema) -> a column mapping -> apply_mapping (typed fields, or a row issue). Without
a mapping the definition's header aliases are used; if they cannot identify an invoice the file is
refused, saying why, and a mapping can be given instead (see tools.map_columns).
"""
from pathlib import Path

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.fields.mapping import (
    ColumnMapping,
    alias_mapping,
    apply_mapping,
    cannot_run,
    runtime_definition,
)
from tickmark.fields.sheet import SampleFormatError, read_sheet
from tickmark.fields.types import clean_text
from tickmark.log import get
from tickmark.models import SampleLoadResult

log = get("mapping")

__all__ = ["SampleFormatError", "clean_text", "load_sample"]


def load_sample(path: str | Path, sheet: str | None = None, definition: Definition = INVOICE_VOUCHING,
                mapping: ColumnMapping | None = None) -> SampleLoadResult:
    data = read_sheet(path, sheet)
    mapping = mapping or alias_mapping(data, definition)
    by_col = {h.col: h.header for h in data.headers}
    log.info("mapping (%s%s): %s", mapping.source, f", confirmed by {mapping.confirmed_by}" if mapping.confirmed_by
             else "", ", ".join(f"{c} {by_col.get(c)!r} → {f}" for f, c in mapping.columns.items()) or "nothing")
    for name, m in mapping.meanings.items():
        log.info("own meaning %s: %s, printed as %s", name, m.type, " / ".join(m.labels))
    for f, note in mapping.notes.items():
        if f not in mapping.columns:
            log.info("%s not mapped: %s", f, note)
    unused = [f"{h.col} {h.header!r}" for h in data.headers if h.col not in mapping.columns.values()]
    if unused:
        log.info("columns kept but not checked: %s", ", ".join(unused))
    if problems := cannot_run(runtime_definition(definition, mapping), mapping):
        log.warning("cannot run: %s", "; ".join(problems))
        raise SampleFormatError("; ".join(problems))
    loaded = apply_mapping(data, definition, mapping)
    log.info("rows: %d loaded, %d errors, %d skipped", len(loaded.rows), len(loaded.errors), len(loaded.skipped))
    for e in loaded.errors:
        log.info("Excel row %d not loaded: %s", e.row_number, e.message)
    return loaded
