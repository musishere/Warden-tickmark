"""Read runs back from the audit database, and append human reviews."""
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Engine, select

from tickmark.models import (
    Candidate,
    Comparison,
    ExtractedField,
    ExtractedInvoice,
    Identification,
    Snip,
)
from tickmark.store.db import engine as default_engine
from tickmark.store.schema import (
    documents,
    extractions,
    match_results,
    reviews,
    run_documents,
    runs,
    sample_issues,
    sample_rows,
)

DECISIONS = ("accept", "choose_candidate", "reject", "requested_from_client")


@dataclass
class Review:
    reviewer: str
    decision: str
    chosen_file: str | None
    note: str
    decided_at: object


@dataclass
class StoredRun:
    run: dict
    sample: dict  # the sample document row
    files: list[dict]  # file_name, document_id, sha256, storage_path, extraction_id
    extracted: list[ExtractedInvoice]
    results: list[Identification]
    issues: list[dict]
    reviews: dict[str, list[Review]] = field(default_factory=dict)  # by row_id, oldest first


def list_runs(db: Engine | None = None, limit: int = 20) -> list[dict]:
    with (db or default_engine()).connect() as conn:
        rows = conn.execute(select(runs).order_by(runs.c.started_at.desc()).limit(limit)).mappings()
        return [dict(r) for r in rows]


def get_run(run_id: UUID | str, db: Engine | None = None) -> StoredRun:
    with (db or default_engine()).connect() as conn:
        run = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().one_or_none()
        if run is None:
            raise KeyError(f"no run {run_id}")
        sample = conn.execute(select(documents).where(documents.c.id == run["sample_document_id"])
                              ).mappings().one_or_none()
        files = [dict(r) for r in conn.execute(
            select(run_documents.c.file_name, run_documents.c.document_id, run_documents.c.extraction_id,
                   documents.c.sha256, documents.c.storage_path, extractions.c.fields, extractions.c.extractor,
                   extractions.c.model_id, extractions.c.raw_sha256)
            .join(documents, documents.c.id == run_documents.c.document_id)
            .join(extractions, extractions.c.id == run_documents.c.extraction_id)
            .where(run_documents.c.run_id == run_id).order_by(run_documents.c.file_name)).mappings()]
        name_of = {f["document_id"]: f["file_name"] for f in files}
        extracted = [ExtractedInvoice(
            file=f["file_name"], extractor=f["extractor"], model_id=f["model_id"],
            fields={k: ExtractedField.model_validate(v) for k, v in f["fields"].items()}) for f in files]

        rows = conn.execute(
            select(match_results, sample_rows.c.row_id, sample_rows.c.excel_row)
            .join(sample_rows, sample_rows.c.id == match_results.c.sample_row_id)
            .where(match_results.c.run_id == run_id).order_by(sample_rows.c.excel_row)).mappings().all()
        results = [Identification(
            row_id=r["row_id"], excel_row=r["excel_row"], status=r["status"],
            invoice_file=r["evidence"]["invoice_file"],
            evidence=r["evidence"]["lines"], reason=r["reason"],
            candidates=[Candidate(invoice_file=c["invoice_file"], agree=c["agree"], note=c["note"])
                        for c in r["candidates"]],
            snips=[Snip.model_validate(s) for s in r["evidence"]["snips"]],
            comparisons=[Comparison.model_validate(c) for c in r["comparisons"]],
            steps=r["steps"]) for r in rows]
        result_row = {r["id"]: r["row_id"] for r in rows}

        issues = [dict(r) for r in conn.execute(select(sample_issues).where(sample_issues.c.run_id == run_id)
                                                .order_by(sample_issues.c.excel_row)).mappings()]
        by_row: dict[str, list[Review]] = {}
        for rv in conn.execute(select(reviews).where(reviews.c.match_result_id.in_(list(result_row)))
                               .order_by(reviews.c.decided_at, reviews.c.id)).mappings():
            by_row.setdefault(result_row[rv["match_result_id"]], []).append(Review(
                rv["reviewer"], rv["decision"], name_of.get(rv["chosen_document_id"]), rv["note"],
                rv["decided_at"]))
    return StoredRun(dict(run), dict(sample) if sample else {}, files, extracted, results, issues, by_row)


def add_review(run_id: UUID | str, row_id: str, *, reviewer: str, decision: str,
               chosen_file: str | None = None, note: str = "", db: Engine | None = None) -> UUID:
    """Append a human decision for one row of a run. Never changes the tool's result."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    with (db or default_engine()).begin() as conn:
        found = conn.execute(
            select(match_results.c.id).join(sample_rows, sample_rows.c.id == match_results.c.sample_row_id)
            .where(match_results.c.run_id == run_id, sample_rows.c.row_id == row_id)).scalars().all()
        if len(found) != 1:
            raise KeyError(f"run {run_id} has {len(found)} rows with id {row_id!r}")
        chosen = None
        if chosen_file:
            chosen = conn.execute(select(run_documents.c.document_id).where(
                run_documents.c.run_id == run_id, run_documents.c.file_name == chosen_file)).scalar_one_or_none()
            if chosen is None:
                raise KeyError(f"{chosen_file!r} was not part of run {run_id}")
        return conn.execute(reviews.insert().values(
            match_result_id=found[0], reviewer=reviewer, decision=decision, chosen_document_id=chosen, note=note,
        ).returning(reviews.c.id)).scalar_one()
