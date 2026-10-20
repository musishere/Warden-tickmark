"""Read the auditor's Excel sample into SampleRow objects.

read_sheet (no schema) -> a column mapping -> apply_mapping (typed fields, or a row issue). Without
a mapping the definition's header aliases are used; if they don't cover every required field the
file is refused, naming the fields, and a mapping can be given instead (see tools.map_columns).
"""
from pathlib import Path

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.fields.mapping import ColumnMapping, alias_mapping, apply_mapping, missing_required
from tickmark.fields.sheet import SampleFormatError, read_sheet
from tickmark.fields.types import clean_text
from tickmark.models import SampleLoadResult

__all__ = ["SampleFormatError", "clean_text", "load_sample"]


def load_sample(path: str | Path, sheet: str | None = None, definition: Definition = INVOICE_VOUCHING,
                mapping: ColumnMapping | None = None) -> SampleLoadResult:
    data = read_sheet(path, sheet)
    mapping = mapping or alias_mapping(data, definition)
    missing = missing_required(definition, mapping)
    if missing:
        raise SampleFormatError(f"missing required column(s): {', '.join(missing)}")
    return apply_mapping(data, definition, mapping)
