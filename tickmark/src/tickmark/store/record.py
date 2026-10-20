"""Write one identification run to the audit database, completely or not at all, inside one
tenant + engagement (see tickmark.store.context)."""
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import UUID

from sqlalchemy import Connection, Engine, select
from sqlalchemy.dialects.postgresql import insert

from tickmark.definitions import DEFINITIONS, INVOICE_VOUCHING
from tickmark.match.identify import THRESHOLDS
from tickmark.models import ExtractedInvoice, Identification
from tickmark.pipeline import IdentificationRun
from tickmark.store import evidence
from tickmark.store.context import TenantContext, scoped
from tickmark.store.mappings import insert_mapping
from tickmark.store.schema import (
    documents,
    extractions,
    match_results,
    run_documents,
    runs,
    sample_issues,
    sample_rows,
    verdicts,
)


def matcher_version() -> str:
    try:
        pkg = version("tickmark")
    except PackageNotFoundError:
        pkg = "unknown"
    here = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=here, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", str(here.parents[1])], cwd=here,
                               capture_output=True, text=True, check=True).stdout.strip()
        return f"tickmark {pkg} @ {commit}{'+dirty' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return f"tickmark {pkg} @ no-git"


def canonical_sha256(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
                          .encode()).hexdigest()


def _scope(ctx: TenantContext) -> dict:
    return {"tenant_id": ctx.tenant_id, "engagement_id": ctx.engagement_id}


def _document(conn: Connection, ctx: TenantContext, path: Path, kind: str, pages: int | None = None) -> UUID:
    """The file, by hash, within this engagement (never shared with another engagement)."""
    sha, stored = evidence.put(path, ctx.tenant_id, ctx.engagement_id)
    conn.execute(insert(documents).values(**_scope(ctx), sha256=sha, kind=kind, size_bytes=Path(path).stat().st_size,
                                          pages=pages, storage_path=str(stored))
                 .on_conflict_do_nothing(constraint="documents_once_per_engagement"))
    return conn.execute(select(documents.c.id).where(
        documents.c.tenant_id == ctx.tenant_id, documents.c.engagement_id == ctx.engagement_id,
        documents.c.sha256 == sha)).scalar_one()


def _extraction(conn: Connection, ctx: TenantContext, document_id: UUID, inv: ExtractedInvoice) -> UUID:
    raw = inv.raw if inv.raw is not None else {}
    values = {**_scope(ctx), "document_id": document_id, "extractor": inv.extractor or "unknown",
              "model_id": inv.model_id, "api_version": inv.api_version, "raw_sha256": canonical_sha256(raw),
              "raw": raw, "fields": {k: f.model_dump(mode="json") for k, f in inv.fields.items()},
              "line_items": [li.model_dump(mode="json") for li in inv.line_items]}
    conn.execute(insert(extractions).values(**values).on_conflict_do_nothing(constraint="extractions_once"))
    return conn.execute(select(extractions.c.id).where(
        extractions.c.document_id == document_id, extractions.c.extractor == values["extractor"],
        extractions.c.model_id == values["model_id"], extractions.c.raw_sha256 == values["raw_sha256"],
    )).scalar_one()


def _pages(inv: ExtractedInvoice) -> int | None:
    raw = inv.raw or {}
    pages = raw.get("analyzeResult", raw).get("pages") if inv.extractor == "azure-docint" else None
    return len(pages) if pages else None


def _result_values(r: Identification, run_id: UUID, sample_row_id: UUID, doc_ids: dict[str, UUID]) -> dict:
    return {
        "run_id": run_id, "sample_row_id": sample_row_id, "status": r.status,
        "document_id": doc_ids[r.invoice_file] if r.invoice_file else None,
        "page": r.snips[0].page if r.snips else None,
        "evidence": {"invoice_file": r.invoice_file, "lines": r.evidence,
                     "snips": [s.model_dump(mode="json") for s in r.snips]},
        "candidates": [{**c.model_dump(mode="json"), "document_id": str(doc_ids[c.invoice_file])}
                       for c in r.candidates],
        "reason": r.reason,
        "comparisons": [c.model_dump(mode="json") for c in r.comparisons],
        "steps": r.steps,
    }


def save_run(run: IdentificationRun, ctx: TenantContext, *, started_at: datetime, db: Engine | None = None) -> UUID:
    """Store a finished run in ctx's engagement: documents (by hash), extractions, rows, issues,
    results. One transaction: all of it or nothing."""
    loaded = run.loaded
    definition = DEFINITIONS[run.cleaned.rows[0].row.definition] if run.cleaned.rows else INVOICE_VOUCHING
    model_ids = sorted({inv.model_id for inv in run.extracted if inv.model_id})
    scope = _scope(ctx)
    with scoped(ctx, db) as conn:
        sample_doc = _document(conn, ctx, run.sample_path, "sample")
        mapping_id = insert_mapping(conn, ctx, loaded.mapping, loaded.headers) if loaded.mapping else None
        run_id = conn.execute(runs.insert().values(
            **scope, run_by=ctx.user, started_at=started_at,
            finished_at=datetime.now(UTC), status="completed", sample_document_id=sample_doc,
            sheet=loaded.sheet, matcher_version=matcher_version(),
            definition=definition.snapshot(), mapping_id=mapping_id,
            thresholds={**THRESHOLDS, "vouch": run.policy.model_dump(mode="json") if run.policy else None},
            extractor=",".join(sorted({inv.extractor for inv in run.extracted})) or "none",
            model_id=",".join(model_ids) or None,
        ).returning(runs.c.id)).scalar_one()

        doc_ids: dict[str, UUID] = {}
        for path, inv in zip(run.invoice_files, run.extracted, strict=True):
            doc_id = _document(conn, ctx, path, "pdf", _pages(inv))
            doc_ids[inv.file] = doc_id
            conn.execute(run_documents.insert().values(**scope, run_id=run_id, document_id=doc_id, file_name=inv.file,
                                                       extraction_id=_extraction(conn, ctx, doc_id, inv)))

        row_ids: dict[int, UUID] = {}
        flags = {cr.row.excel_row: cr for cr in run.cleaned.rows}
        for row in run.loaded.rows:
            cr = flags[row.excel_row]
            row_ids[row.excel_row] = conn.execute(sample_rows.insert().values(
                **scope, run_id=run_id, row_id=row.row_id, excel_row=row.excel_row,
                raw=[c.model_dump(mode="json") for c in row.raw],
                fields={k: v.model_dump(mode="json") for k, v in row.fields.items()},
                vendor_key=cr.vendor_key, flags=cr.flags,
            ).returning(sample_rows.c.id)).scalar_one()
        issues = [(e.row_number, "error", e.message, e.raw) for e in loaded.errors]
        issues += [(s.row_number, "skipped", s.reason, s.raw) for s in loaded.skipped]
        for n, kind, reason, cells in issues:
            conn.execute(sample_issues.insert().values(**scope, run_id=run_id, excel_row=n, kind=kind, reason=reason,
                                                       raw=[c.model_dump(mode="json") for c in cells]))

        result_ids: dict[int, UUID] = {}
        for r in run.results:
            result_ids[r.excel_row] = conn.execute(match_results.insert().values(
                **scope, **_result_values(r, run_id, row_ids[r.excel_row], doc_ids)
            ).returning(match_results.c.id)).scalar_one()

        for v in run.verdicts:
            conn.execute(verdicts.insert().values(
                **scope, run_id=run_id, match_result_id=result_ids[v.excel_row], verdict=v.verdict,
                document_id=doc_ids[v.invoice_file] if v.invoice_file else None,
                agreements=[a.model_dump(mode="json") for a in v.agreements],
                checks=[c.model_dump(mode="json") for c in v.checks], reasons=v.reasons, steps=v.steps))
    return run_id


def save_failed_run(ctx: TenantContext, *, sample_path: Path | None, started_at: datetime, error: str,
                    extractor: str = "unknown", db: Engine | None = None) -> UUID:
    """A run that crashed is still recorded, with what is known about it."""
    with scoped(ctx, db) as conn:
        sample_doc = (_document(conn, ctx, sample_path, "sample")
                      if sample_path and Path(sample_path).is_file() else None)
        return conn.execute(runs.insert().values(
            **_scope(ctx), run_by=ctx.user, started_at=started_at,
            finished_at=datetime.now(UTC), status="failed", error=error,
            sample_document_id=sample_doc, matcher_version=matcher_version(), thresholds=THRESHOLDS,
            extractor=extractor,
        ).returning(runs.c.id)).scalar_one()
