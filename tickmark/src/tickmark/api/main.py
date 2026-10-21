"""Tickmark's web app: the whole flow behind JSON endpoints, and one page that drives it (GET /).

upload sample + PDFs -> settle the column meanings (known names, LLM suggestion, by hand) -> run
(PDFs read locally, Azure only where needed) -> verdicts, how each PDF was read, and the run's log.
Saving to the audit trail is optional (a firm + engagement). The firm and engagement come from the
request here: a local stand-in. In production they come from the Warden-verified token. No auth.
"""
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from tickmark import log
from tickmark.api import jobs, workspace
from tickmark.config import settings
from tickmark.definitions import INVOICE_VOUCHING
from tickmark.fields.mapping import ColumnMapping, Meaning, headers_sha256, validate
from tickmark.fields.types import parse_currency
from tickmark.store import DECISIONS, TenantContext
from tickmark.vouch import VouchPolicy, parse_period

log.setup()
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Tickmark")
app.state.db = None  # the app's database engine; tests point it at a throw-away database
app.state.admin_db = None
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(OperationalError)
def db_down(_, e: OperationalError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": "the audit database is not reachable (docker compose up -d)"}, status_code=503)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def page() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {"azure": settings.azure, "llm_mapping": settings.llm_mapping, "decisions": DECISIONS,
            "azure_configured": bool(settings.docint_endpoint and settings.docint_key)}


def _ctx(tenant: str | None, engagement: str | None, user: str | None) -> TenantContext | None:
    if not (tenant and engagement):
        return None
    try:
        return TenantContext(tenant, engagement, user or "web")
    except ValueError as e:
        raise HTTPException(400, f"bad firm or engagement id: {e}") from None


# --- upload and column meanings -------------------------------------------------------------------

@app.post("/api/workspaces")
async def create_workspace(sample: UploadFile = File(...), invoices: list[UploadFile] = File(default=[]),  # noqa: B008
                           tenant: str | None = Form(None), engagement: str | None = Form(None),
                           user: str | None = Form(None)) -> dict:
    ws = await workspace.create(sample, invoices, _ctx(tenant, engagement, user), app.state.db)
    return workspace.view(ws)


@app.get("/api/workspaces/{ws_id}")
def get_workspace(ws_id: str) -> dict:
    return workspace.view(workspace.get(ws_id))


@app.post("/api/workspaces/{ws_id}/suggest")
def suggest(ws_id: str) -> dict:
    """The LLM proposes meanings for the columns still unmapped (headers + value patterns only)."""
    ws = workspace.get(ws_id)
    if not settings.llm_mapping:
        raise HTTPException(400, "LLM mapping is off: set TICKMARK_LLM_MAPPING=on in .env")
    from tickmark.fields.llm_mapping import LLMNotConfigured, LLMProposer

    try:
        p = LLMProposer().propose(ws.sheet(), INVOICE_VOUCHING)
    except LLMNotConfigured as e:
        raise HTTPException(400, str(e)) from None
    if p is None:
        raise HTTPException(502, "the LLM gave no proposal (refused or failed): map by hand")
    m = ws.mapping
    columns, meanings, notes = dict(m.columns), dict(m.meanings), dict(m.notes)
    for pf in p.fields:
        notes[pf.field] = f"{pf.reason} (confidence {pf.confidence:.2f})"
        if pf.column and pf.field not in columns and pf.column not in columns.values():
            columns[pf.field] = pf.column
            ws.origin[pf.field] = "LLM"
            if pf.field in p.meanings:
                meanings[pf.field] = p.meanings[pf.field]
    ws.mapping = m.model_copy(update={"columns": columns, "meanings": meanings, "notes": notes, "source": "claude",
                                      "proposal": p.model_dump(mode="json"), "confirmed_by": None})
    return workspace.view(ws)


class MappingIn(BaseModel):
    columns: dict[str, str]  # meaning -> column letter
    meanings: dict[str, Meaning] = {}  # the layout's own meanings
    confirmed_by: str


@app.put("/api/workspaces/{ws_id}/mapping")
def set_mapping(ws_id: str, body: MappingIn) -> dict:
    """Check the mapping against every value of the sheet; confirm it if it is usable."""
    ws = workspace.get(ws_id)
    sheet = ws.sheet()
    before = ws.mapping
    for f, c in body.columns.items():
        if before.columns.get(f) != c:
            ws.origin[f] = "you"
    m = ColumnMapping(definition=INVOICE_VOUCHING.name, version=INVOICE_VOUCHING.version, columns=body.columns,
                      meanings=body.meanings, source="claude" if "LLM" in ws.origin.values() else "person",
                      headers_sha256=headers_sha256(sheet), notes=before.notes, proposal=before.proposal)
    ws.mapping = m
    if problems := validate(sheet, INVOICE_VOUCHING, m):
        return workspace.view(ws, problems) | {"confirmed": False}
    ws.mapping = m.model_copy(update={"confirmed_by": body.confirmed_by or "web"})
    saved = None
    if ws.ctx:
        from tickmark.store.mappings import save_mapping

        saved = str(save_mapping(ws.ctx, ws.mapping, sheet.headers, db=app.state.db))
    return workspace.view(ws, []) | {"confirmed": True, "saved_mapping": saved}


# --- runs -----------------------------------------------------------------------------------------

class RunIn(BaseModel):
    period: str | None = None  # YYYY-MM-DD:YYYY-MM-DD; with an engagement, its period by default
    currency: str | None = None  # with an engagement, its currency by default
    azure: str = settings.azure  # auto | off | always
    line_items: bool | None = None  # with an engagement, its setting by default; else off
    save: bool = False  # store the run in the engagement's audit trail


@app.post("/api/workspaces/{ws_id}/runs")
def start_run(ws_id: str, body: RunIn) -> dict:
    ws = workspace.get(ws_id)
    if body.azure not in ("auto", "off", "always"):
        raise HTTPException(400, "azure must be auto, off or always")
    if ws.mapping is None or validate(ws.sheet(), INVOICE_VOUCHING, ws.mapping):
        raise HTTPException(400, "settle the column meanings first")
    if body.save and not ws.ctx:
        raise HTTPException(400, "to save, upload with a firm and engagement")
    try:
        period = parse_period(body.period) if body.period else (None, None)
        currency = parse_currency(body.currency) if body.currency else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    line_items = body.line_items
    if ws.ctx:  # the engagement's settings where the page leaves them empty
        from tickmark.store import engagement_currency, engagement_line_items, engagement_period

        if period == (None, None):
            period = engagement_period(ws.ctx, app.state.db)
        currency = currency or engagement_currency(ws.ctx, app.state.db)
        if line_items is None:
            line_items = engagement_line_items(ws.ctx, app.state.db)
    policy = VouchPolicy(period_start=period[0], period_end=period[1], currency=currency, line_items=bool(line_items))
    job = jobs.start(ws, policy, body.azure, body.save, app.state.db)
    return {"job": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, since: int = 0) -> dict:
    job = jobs.get(job_id)
    return {"status": job.status, "logs": job.logs[since:], "next": len(job.logs),
            "result": job.result if job.status == "done" else None, "error": job.error}


@app.get("/api/workspaces/{ws_id}/pdf/{name}")
def pdf(ws_id: str, name: str) -> FileResponse:
    path = workspace.get(ws_id).invoices / Path(name).name
    if not path.is_file():
        raise HTTPException(404, f"no PDF {name}")
    return FileResponse(path, media_type="application/pdf")


# --- firms, engagements, stored runs (a local stand-in for Warden) --------------------------------

class TenantIn(BaseModel):
    name: str


class EngagementIn(BaseModel):
    client_name: str
    period: str | None = None
    currency: str | None = None
    line_items: bool = False


@app.get("/api/tenants")
def tenants() -> list[dict]:
    from tickmark.store.provision import list_tenants

    firms: dict[str, dict] = {}
    for r in list_tenants(app.state.admin_db):
        firm = firms.setdefault(str(r["id"]), {"id": str(r["id"]), "name": r["name"], "engagements": []})
        if r["engagement_id"]:
            firm["engagements"].append(jsonable_encoder({k: r[k] for k in (
                "engagement_id", "client_name", "period_start", "period_end", "currency", "line_items")}))
    return list(firms.values())


@app.post("/api/tenants")
def new_tenant(body: TenantIn) -> dict:
    from tickmark.store.provision import add_tenant

    return {"id": str(add_tenant(body.name, db=app.state.admin_db))}


@app.post("/api/tenants/{tenant_id}/engagements")
def new_engagement(tenant_id: str, body: EngagementIn) -> dict:
    from tickmark.store.provision import add_engagement, set_line_items

    try:
        period = parse_period(body.period) if body.period else None
        eid = add_engagement(UUID(tenant_id), body.client_name, period=period, currency=body.currency or None,
                             db=app.state.admin_db)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if body.line_items:
        set_line_items(eid, True, db=app.state.admin_db)
    return {"id": str(eid)}


@app.get("/api/engagements/{engagement_id}/runs")
def runs(engagement_id: str, tenant: str, user: str = "web") -> list[dict]:
    from tickmark.store import list_runs

    return jsonable_encoder(list_runs(_ctx(tenant, engagement_id, user), app.state.db))


@app.get("/api/runs/{run_id}")
def stored_run(run_id: str, tenant: str, engagement: str, user: str = "web") -> dict:
    from tickmark.store import get_run

    r = get_run(_ctx(tenant, engagement, user), run_id, app.state.db)
    return jsonable_encoder({"run": r.run, "files": r.files, "verdicts": r.verdicts, "identifications": r.results,
                             "issues": r.issues, "reviews": r.reviews, "mapping": r.mapping},
                            custom_encoder={bytes: lambda b: b.hex()})


class ReviewIn(BaseModel):
    tenant: str
    engagement: str
    user: str
    row_id: str
    decision: str
    chosen_file: str | None = None
    note: str = ""


@app.post("/api/runs/{run_id}/reviews")
def review(run_id: str, body: ReviewIn) -> dict:
    from tickmark.store import add_review

    try:
        rid = add_review(_ctx(body.tenant, body.engagement, body.user), run_id, body.row_id, decision=body.decision,
                         chosen_file=body.chosen_file, note=body.note, db=app.state.db)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return {"id": str(rid)}
