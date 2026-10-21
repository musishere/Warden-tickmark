"""An upload: the sample and its invoice PDFs in data/uploads/<id>/, and the column mapping as it is
being settled. Kept in memory (a local dev tool); the files stay on disk."""
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException, UploadFile

from tickmark.config import settings
from tickmark.definitions import INVOICE_VOUCHING
from tickmark.fields.mapping import (
    ColumnMapping,
    alias_mapping,
    cannot_run,
    resolve,
    runtime_definition,
)
from tickmark.fields.profile import profile_sheet
from tickmark.fields.sheet import Sheet, read_sheet
from tickmark.fields.types import TYPES
from tickmark.store import TenantContext

ALLOWED = {".xlsx", ".pdf"}


@dataclass
class Workspace:
    id: str
    dir: Path
    ctx: TenantContext | None = None  # set when runs are saved to an engagement
    mapping: ColumnMapping | None = None  # the mapping as it is now (confirmed once confirmed_by is set)
    origin: dict[str, str] = field(default_factory=dict)  # field -> known name | saved | LLM | you

    @property
    def sample(self) -> Path:
        return self.dir / "sample.xlsx"

    @property
    def invoices(self) -> Path:
        return self.dir / "invoices"

    def sheet(self) -> Sheet:
        return read_sheet(self.sample)


WORKSPACES: dict[str, Workspace] = {}


def get(ws_id: str) -> Workspace:
    if ws_id not in WORKSPACES:
        raise HTTPException(404, f"no upload {ws_id}")
    return WORKSPACES[ws_id]


async def _save(upload: UploadFile, to: Path) -> None:
    to.write_bytes(await upload.read())


async def create(sample: UploadFile, invoices: list[UploadFile], ctx: TenantContext | None, db=None) -> Workspace:
    """Store the files (only .xlsx / .pdf, by their base name) and start from the best known mapping."""
    for f in [sample, *invoices]:
        if Path(f.filename or "").suffix.lower() not in ALLOWED:
            raise HTTPException(400, f"{f.filename!r}: only .xlsx samples and .pdf invoices are accepted")
    if Path(sample.filename).suffix.lower() != ".xlsx":
        raise HTTPException(400, "the sample must be an .xlsx file")
    ws_id = uuid.uuid4().hex[:12]
    ws = Workspace(id=ws_id, dir=Path(settings.upload_dir) / ws_id, ctx=ctx)
    ws.invoices.mkdir(parents=True)
    await _save(sample, ws.sample)
    for f in invoices:
        await _save(f, ws.invoices / Path(f.filename).name)  # base name only: no ../ out of the folder
    sheet = ws.sheet()
    saved = None
    if ctx:
        from tickmark.fields.mapping import headers_sha256
        from tickmark.store.mappings import current_mapping

        saved = current_mapping(ctx, INVOICE_VOUCHING.name, INVOICE_VOUCHING.version, headers_sha256(sheet), db)
    usable, _ = resolve(sheet, INVOICE_VOUCHING, saved)
    ws.mapping = usable or alias_mapping(sheet, INVOICE_VOUCHING)
    origin = "saved" if usable is not None and usable.source == "saved" else "known name"
    ws.origin = dict.fromkeys(ws.mapping.columns, origin)
    WORKSPACES[ws.id] = ws
    return ws


def view(ws: Workspace, problems: list[str] | None = None) -> dict:
    """What the page shows for an upload: the sheet's columns, the mapping now, and what is missing."""
    sheet = ws.sheet()
    m = ws.mapping
    definition = runtime_definition(INVOICE_VOUCHING, m)
    if problems is None:
        problems = cannot_run(definition, m)
    profiles = {p.col: p for p in profile_sheet(sheet)}
    return {
        "id": ws.id, "sheet": sheet.title, "header_row": sheet.header_row, "rows": len(sheet.rows),
        "above_header": len(sheet.preamble), "engagement": str(ws.ctx.engagement_id) if ws.ctx else None,
        "tenant": str(ws.ctx.tenant_id) if ws.ctx else None,
        "columns": [{"col": h.col, "header": h.header, "pattern": profiles[h.col].pattern,
                     "filled": profiles[h.col].filled} for h in sheet.headers],
        "catalogue": [{"name": f.name, "type": f.type, "role": f.role, "description": f.description}
                      for f in INVOICE_VOUCHING.fields],
        "types": list(TYPES),
        "mapping": {"columns": m.columns, "meanings": {n: x.model_dump() for n, x in m.meanings.items()},
                    "notes": m.notes, "source": m.source, "confirmed_by": m.confirmed_by},
        "origin": ws.origin, "problems": problems,
        "pdfs": sorted(p.name for p in ws.invoices.glob("*.pdf")),
    }
