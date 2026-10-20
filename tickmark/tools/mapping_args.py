"""--mapping for the tools, and the one rule for which column mapping a run uses:
a confirmed file given with --mapping; else the mapping saved for this engagement and header
layout; else the known header names. If required fields are still unmapped, the run does not
start: it says which, and how to map them (tools.map_columns).
"""
import argparse
import json
from pathlib import Path

from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.fields.mapping import ColumnMapping, headers_sha256, resolve
from tickmark.fields.sheet import read_sheet
from tickmark.store import TenantContext


class NeedsMapping(SystemExit):
    pass


def add_mapping_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--mapping", type=Path, help="a confirmed column mapping (JSON from tools.map_columns --out)")


def choose_mapping(sample: Path, mapping_file: Path | None, ctx: TenantContext | None,
                   definition: Definition = INVOICE_VOUCHING) -> ColumnMapping:
    sheet = read_sheet(sample)
    given = ColumnMapping.model_validate(json.loads(mapping_file.read_text())) if mapping_file else None
    if given is not None and not given.confirmed_by:
        raise NeedsMapping(f"{mapping_file}: this mapping was never confirmed (tools.map_columns --confirm)")
    saved = None
    if ctx is not None:
        from tickmark.store.mappings import current_mapping

        saved = current_mapping(ctx, definition.name, definition.version, headers_sha256(sheet))
    mapping, missing = resolve(sheet, definition, saved, given)
    if mapping is None:
        scope = f" --tenant {ctx.tenant_id} --engagement {ctx.engagement_id}" if ctx else " --out mapping.json"
        raise NeedsMapping(
            f"needs mapping: no column is known for {', '.join(missing)}.\n"
            f"  headers: {', '.join(f'{h.col} {h.header!r}' for h in sheet.headers)}\n"
            f"  map it once:  python -m tools.map_columns {sample} [--suggest] --set FIELD=COL --confirm{scope}")
    return mapping


def mapping_rows(loaded) -> list[list]:
    """Rows for a workbook's Mapping sheet: field, column, header, source, who confirmed, notes."""
    m = loaded.mapping
    by_col = {h.col: h.header for h in loaded.headers}
    used = set(m.columns.values()) if m else set()
    rows = [[f, c, by_col.get(c), m.source, m.confirmed_by or "", m.notes.get(f, "")] for f, c in m.columns.items()]
    rows += [["(not used)", h.col, h.header, "", "", "kept in raw, no rule reads it"]
             for h in loaded.headers if h.col not in used]
    return rows
