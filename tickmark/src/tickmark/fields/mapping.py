"""Their columns -> our fields. A mapping says which column of a sheet holds each field of an audit
definition; applying it turns raw cells into typed values (or a row issue with a reason).

Where a mapping comes from (recorded as `source`): a saved, confirmed one for the same client and
header row; header aliases; a proposal (an LLM) confirmed by a person; or a person alone.
"""
import hashlib
import json
from functools import partial
from typing import Literal

from pydantic import BaseModel, Field

from tickmark.definitions import Definition, FieldSpec
from tickmark.fields.sheet import CODE_IN_BRACKETS, Sheet, header_name, is_blank, padded
from tickmark.fields.types import TYPES, clean_text, column_decimal
from tickmark.models import FieldValue, RowError, SampleLoadResult, SampleRow, SkippedRow

FOOTER_WORDS = {"total", "subtotal", "sub total", "grand total", "sum", "totals", "notes", "note"}
MIN_PARSE_RATE = 0.8  # a proposed column must parse as its type for at least this share of its values


class ColumnMapping(BaseModel):
    definition: str
    version: int
    columns: dict[str, str]  # field name -> column letter
    source: Literal["aliases", "saved", "claude", "person"]
    confirmed_by: str | None = None
    headers_sha256: str = ""  # which header row it was made for
    notes: dict[str, str] = Field(default_factory=dict)  # field -> why (a proposal's reasons)
    proposal: dict | None = None  # what was sent to and returned by the LLM, kept for the audit trail


def headers_sha256(sheet: Sheet) -> str:
    """Identifies a header layout: same columns, same headers, same order. Brackets count:
    'Amount (EUR)' and 'Amount (USD)' are different layouts."""
    names = [[h.col, clean_text(h.header).casefold()] for h in sheet.headers]
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()


def alias_mapping(sheet: Sheet, definition: Definition) -> ColumnMapping:
    """Columns whose header is a known name of a field. May leave fields unmapped. A field that
    two or more columns could hold ('Amount' and 'Total') is not guessed: it stays unmapped, with
    the candidates in notes, for a person (or a proposal they confirm) to choose."""
    found: dict[str, list] = {}
    for h in sheet.headers:
        name = header_name(h.header)
        for f in definition.fields:
            if name in f.aliases:
                found.setdefault(f.name, []).append(h)
    columns = {f: hs[0].col for f, hs in found.items() if len(hs) == 1}
    notes = {f: "ambiguous: could be " + " or ".join(f"{h.col} {h.header!r}" for h in hs)
             for f, hs in found.items() if len(hs) > 1}
    return ColumnMapping(definition=definition.name, version=definition.version, columns=columns,
                         source="aliases", headers_sha256=headers_sha256(sheet), notes=notes)


def missing_required(definition: Definition, mapping: ColumnMapping) -> list[str]:
    return [f.name for f in definition.required if f.name not in mapping.columns]


def describe_missing(definition: Definition, mapping: ColumnMapping) -> list[str]:
    """missing_required, each with why when known: 'amount (ambiguous: could be C 'Amount' or D 'Total')'."""
    return [f"{f} ({mapping.notes[f]})" if f in mapping.notes else f for f in missing_required(definition, mapping)]


def _footer_label(values: list) -> str | None:
    """A row whose first non-empty cell is a word like 'Total' is a footer, not data."""
    first = next((v for v in values if v not in (None, "")), None)
    if isinstance(first, str) and clean_text(first).lower().rstrip(":") in FOOTER_WORDS:
        return clean_text(first)
    return None


def _data_rows(sheet: Sheet):
    for row in sheet.rows:
        if not is_blank(row.values) and not _footer_label(row.values):
            yield row


def column_values(sheet: Sheet, col: str) -> list:
    """The non-empty values of one column, data rows only."""
    i = next(n for n, h in enumerate(sheet.headers) if h.col == col)
    return [v for row in _data_rows(sheet) if (v := padded(row.values, len(sheet.headers))[i]) not in (None, "")]


def parser(sheet: Sheet, spec: FieldSpec, col: str):
    """How this column's cells are read as the field's type. Money learns the column's decimal mark
    from all its values, so '1.234' is 1234 in a '1.234,50' column and is refused when nothing tells."""
    parse = TYPES[spec.type].parse
    return partial(parse, decimal=column_decimal(column_values(sheet, col))) if spec.type == "money" else parse


def header_value(sheet: Sheet, spec: FieldSpec, mapping: ColumnMapping):
    """A field given in brackets in another column's header: 'Amount (USD)' -> 'USD' for currency.
    Only as written in capitals, so 'Amount (net)' is not read as a currency 'NET'."""
    col = mapping.columns.get(spec.from_header_of) if spec.from_header_of else None
    header = next((h.header for h in sheet.headers if h.col == col), None) if col else None
    for inner in CODE_IN_BRACKETS.findall(header or ""):
        try:
            return TYPES[spec.type].parse(inner, spec.label), col
        except ValueError:
            pass
    return None, None


def validate(sheet: Sheet, definition: Definition, mapping: ColumnMapping) -> list[str]:
    """Problems with a mapping, checked against every value of the sheet. Empty = usable."""
    problems: list[str] = []
    cols = {h.col for h in sheet.headers}
    names = {f.name for f in definition.fields}
    for field, col in mapping.columns.items():
        if field not in names:
            problems.append(f"{field}: not a field of {definition.name}")
        elif col not in cols:
            problems.append(f"{field}: column {col} does not exist")
    used = [c for c in mapping.columns.values()]
    problems += [f"column {c} is used for more than one field" for c in sorted({c for c in used if used.count(c) > 1})]
    problems += [f"{f}: required but not mapped" for f in missing_required(definition, mapping)]
    for field, col in mapping.columns.items():
        if field not in names or col not in cols:
            continue
        spec = definition.field(field)
        values = column_values(sheet, col)
        if not values:
            problems.append(f"{field}: column {col} has no values")
            continue
        ok = 0
        parse = parser(sheet, spec, col)
        for v in values:
            try:
                parse(v, spec.label)
                ok += 1
            except ValueError:
                pass
        if ok / len(values) < MIN_PARSE_RATE:
            problems.append(f"{field}: only {ok} of {len(values)} values in column {col} read as {spec.type}")
    return problems


def apply_mapping(sheet: Sheet, definition: Definition, mapping: ColumnMapping) -> SampleLoadResult:
    """Every row below the header ends up exactly once: a typed row, an error, or a skipped footer
    (blank rows are ignored). Raw cells are kept on all three; rows above the header are skipped."""
    index = {h.col: n for n, h in enumerate(sheet.headers)}
    parsers = {f.name: parser(sheet, f, mapping.columns[f.name]) for f in definition.fields
               if f.name in mapping.columns}
    from_header = {f.name: header_value(sheet, f, mapping) for f in definition.fields
                   if f.from_header_of and f.name not in mapping.columns}
    rows, errors = [], []
    skipped = [SkippedRow(row_number=r.excel_row, reason=f"above the header row {sheet.header_row}",
                          raw=sheet.cells(r)) for r in sheet.preamble]
    for row in sheet.rows:
        if is_blank(row.values):
            continue
        raw = sheet.cells(row)
        if footer := _footer_label(row.values):
            skipped.append(SkippedRow(row_number=row.excel_row, reason=f"footer row: {footer!r}", raw=raw))
            continue
        values = padded(row.values, len(sheet.headers))
        fields: dict[str, FieldValue] = {}
        row_id = str(row.excel_row)
        try:
            for spec in definition.fields:
                col = mapping.columns.get(spec.name)
                v = values[index[col]] if col else None
                if spec.role == "label":
                    if clean_text(v):
                        row_id = TYPES[spec.type].parse(v, spec.label)
                    continue
                t = TYPES[spec.type]
                if v in (None, "") or (isinstance(v, str) and not clean_text(v)):
                    if col and (row.excel_row, col) in sheet.formulas:
                        raise ValueError(f"{spec.label}: column {col} holds a formula that was never calculated "
                                         "(open and save the file in Excel, then load it again)")
                    if spec.required:
                        t.parse(v, spec.label)  # raises with the type's own message
                    if (hv := from_header.get(spec.name, (None, None)))[0] is not None:
                        fields[spec.name] = FieldValue(type=t.name, value=t.to_str(hv[0]), col=f"header {hv[1]}")
                    continue
                fields[spec.name] = FieldValue(type=t.name, value=t.to_str(parsers[spec.name](v, spec.label)), col=col)
        except ValueError as e:
            errors.append(RowError(row_number=row.excel_row, message=str(e), raw=raw))
            continue
        rows.append(SampleRow(row_id=row_id, excel_row=row.excel_row, definition=definition.name,
                              fields=fields, raw=raw))
    return SampleLoadResult(rows=rows, errors=errors, skipped=skipped, sheet=sheet.title, headers=sheet.headers,
                            mapping=mapping)


def resolve(sheet: Sheet, definition: Definition, saved: ColumnMapping | None = None,
            given: ColumnMapping | None = None) -> tuple[ColumnMapping | None, list[str]]:
    """The mapping a run uses, in order: one given explicitly (a confirmed file); a confirmed
    mapping saved for this exact header layout; the header aliases if they cover every required
    field. Otherwise (None, the fields still missing): the run must not start."""
    for m in (given, saved):
        if m is not None and m.headers_sha256 in ("", headers_sha256(sheet)) and not missing_required(definition, m):
            return m.model_copy(update={"headers_sha256": headers_sha256(sheet)}), []
    aliases = alias_mapping(sheet, definition)
    missing = describe_missing(definition, aliases)
    return (None, missing) if missing else (aliases, [])
