"""A run started from the page: it works in a background thread, under its own log capture, so the
page can show its lines as they happen and its result when it is done."""
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException

from tickmark import log
from tickmark.api.workspace import Workspace
from tickmark.extract.router import Router
from tickmark.pipeline import IdentificationRun, run_vouch
from tickmark.vouch import MARK, VouchPolicy

api_log = log.get("api")


@dataclass
class Job:
    id: str
    status: str = "running"  # running | done | failed
    logs: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None


JOBS: dict[str, Job] = {}


def get(job_id: str) -> Job:
    if job_id not in JOBS:
        raise HTTPException(404, f"no job {job_id}")
    return JOBS[job_id]


def start(ws: Workspace, policy: VouchPolicy, azure: str, save: bool, db=None) -> Job:
    job = Job(id=uuid.uuid4().hex[:12])
    job.logs = log.capture(job.id)
    JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, ws, policy, azure, save, db), daemon=True).start()
    return job


def _run(job: Job, ws: Workspace, policy: VouchPolicy, azure: str, save: bool, db) -> None:
    log.job_id.set(job.id)
    started = datetime.now(UTC)
    api_log.info("run started: %s, %d PDFs, Azure %s, period %s … %s, currency %s, item lines %s",
                 ws.sample.name, len(list(ws.invoices.glob("*.pdf"))), azure, policy.period_start,
                 policy.period_end, policy.currency or "none", "on" if policy.line_items else "off")
    try:
        run = run_vouch(ws.sample, ws.invoices, Router(azure), policy, ws.mapping)
        run_id = None
        if save and ws.ctx:
            from tickmark.store import save_run

            run_id = str(save_run(run, ws.ctx, started_at=started, db=db))
        job.result = result(run, run_id)
        job.status = "done"
        api_log.info("run done: %s", ", ".join(f"{MARK[k]} {n}" for k, n in job.result["counts"].items()))
    except Exception as e:  # noqa: BLE001 - the page shows why the run failed
        api_log.exception("run failed")
        job.error, job.status = f"{e.__class__.__name__}: {e}", "failed"


def result(run: IdentificationRun, run_id: str | None = None) -> dict:
    """Everything the page shows for a finished run."""
    return {
        "run_id": run_id,
        "counts": {k: sum(v.verdict == k for v in run.verdicts) for k in MARK},
        "verdicts": [v.model_dump(mode="json") for v in run.verdicts],
        "identifications": [r.model_dump(mode="json") for r in run.results],
        "routes": [{"file": f, "read_by": by, "why": why} for f, (by, why) in sorted(run.routes.items())],
        "pdfs": [{"file": inv.file, "read_by": inv.extractor, "fields": {k: f.value for k, f in inv.fields.items()},
                  "unread": inv.unread, "needs_ocr": inv.needs_ocr} for inv in run.extracted],
        "errors": [e.model_dump(mode="json") for e in run.loaded.errors],
        "skipped": [s.model_dump(mode="json") for s in run.loaded.skipped],
        "policy": run.policy.model_dump(mode="json") if run.policy else None,
        "mapping": run.loaded.mapping.model_dump(mode="json") if run.loaded.mapping else None,
    }
