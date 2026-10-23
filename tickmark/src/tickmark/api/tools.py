"""Tickmark's tools, behind Warden: list_documents, read_document, run_vouch.

Only Warden may call these. Every request must carry the token Warden's gate forwards
(warden/src/Warden.Api/Auth/AgentPass.cs, CreateForward): signed with Warden's key (checked with
the public key from Warden's JWKS), iss = warden, aud = tickmark-tools, at most a minute old, and
made for THIS tool. An agent's own pass has aud = warden-tools, so an agent calling here directly,
skipping Warden, gets 401. Can't reach Warden's JWKS -> 401 too (fail closed).

Which engagement: ALWAYS the token's `eng` claim, never the request body. Each engagement's files
live in <engagements_dir>/<engagement id>/: sample.xlsx and invoices/*.pdf (`make warden-demo`).

export_documents is deliberately not here: Warden never forwards it for an agent.
"""
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import jwt
import pdfplumber
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from tickmark import log
from tickmark.config import settings
from tickmark.extract.router import Router
from tickmark.fields.types import parse_currency
from tickmark.pipeline import invoice_files, run_vouch
from tickmark.vouch import MARK, VouchPolicy, parse_period

tools_log = log.get("tools")
router = APIRouter(prefix="/tools")
_jwks: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class WardenCall:
    """Who is calling, as Warden vouches for it."""

    tenant: UUID
    engagement: UUID
    user: str
    agent: str
    tool: str

    @property
    def folder(self) -> Path:
        return Path(settings.engagements_dir) / str(self.engagement)


def signing_key(token: str):
    """Warden's public key for this token (fetched from its JWKS once, again on an unknown kid)."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{settings.warden_url}/.well-known/jwks.json")
    return _jwks.get_signing_key_from_jwt(token).key


def warden_claims(authorization: str, what: str) -> dict:
    """The claims of a Warden-forwarded token, or 401. `what` is only for the log."""
    token = authorization.removeprefix("Bearer ")
    try:
        return jwt.decode(token, signing_key(token), algorithms=["RS256"], issuer="warden",
                          audience=settings.tools_audience,
                          options={"require": ["exp", "sub", "act", "tid", "eng", "tool"]})
    except jwt.PyJWTError as e:  # bad signature, expired, wrong audience, Warden unreachable, ...
        tools_log.warning("refused %s: not a Warden-forwarded call (%s)", what, e)
        raise HTTPException(401, "only Warden may call Tickmark's tools") from None


def warden_call(authorization: str, tool: str) -> WardenCall:
    """The request must be a Warden-forwarded call made for exactly `tool`."""
    c = warden_claims(authorization, tool)
    if c["tool"] != tool:
        raise HTTPException(403, f"the token is for {c['tool']}, not {tool}")
    return WardenCall(UUID(c["tid"]), UUID(c["eng"]), c["sub"], c["act"]["sub"], tool)


def from_warden(tool: str) -> Callable[..., WardenCall]:
    """A FastAPI dependency: the request must be a Warden-forwarded call for `tool`."""

    def check(authorization: str = Header("")) -> WardenCall:
        return warden_call(authorization, tool)

    return check


def _pdf(call: WardenCall, name: str) -> Path:
    path = call.folder / "invoices" / Path(name).name  # .name: no "../" out of the engagement's folder
    if not path.is_file():
        raise HTTPException(404, f"no document {name}")
    return path


@router.post("/list_documents")
def list_documents(call: WardenCall = Depends(from_warden("list_documents"))) -> dict:  # noqa: B008
    files = invoice_files(call.folder / "invoices")
    return {"documents": [{"name": p.name, "bytes": p.stat().st_size,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}


class ReadIn(BaseModel):
    name: str


@router.post("/read_document")
def read_document(body: ReadIn, call: WardenCall = Depends(from_warden("read_document"))) -> dict:  # noqa: B008
    path = _pdf(call, body.name)
    with pdfplumber.open(path) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": pages}


class VouchIn(BaseModel):
    period: str | None = None  # YYYY-MM-DD:YYYY-MM-DD
    currency: str | None = None


@router.post("/run_vouch")
def vouch(body: VouchIn, call: WardenCall = Depends(from_warden("run_vouch"))) -> dict:  # noqa: B008
    sample = call.folder / "sample.xlsx"
    if not sample.is_file():
        raise HTTPException(404, "this engagement has no sample.xlsx")
    try:
        start, end = parse_period(body.period) if body.period else (None, None)
        currency = parse_currency(body.currency) if body.currency else None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    # ponytail: own PDF text only (Azure off); the web app's Azure routing when agents need scans read.
    run = run_vouch(sample, call.folder / "invoices", Router("off"),
                    VouchPolicy(period_start=start, period_end=end, currency=currency))
    tools_log.info("run_vouch for %s by agent %s: %d rows", call.user, call.agent, len(run.verdicts))
    return {"verdicts": [{"row_id": v.row_id, "verdict": v.verdict, "mark": MARK[v.verdict],
                          "invoice_file": v.invoice_file, "reasons": v.reasons} for v in run.verdicts]}
