"""Step-by-step logs: every stage of a run says what it decided and why.

One logger per step - tickmark.sheet, .mapping, .llm, .read, .azure, .router, .identify, .vouch,
.store - written to the console and to data/logs/tickmark.log (settings.log_dir, log_level). A run
started by the web app also collects its own lines (capture), so the page shows only that run.
The decisions themselves are recorded in the results (Identification.steps, Verdict.steps); these
logs mirror them as they happen.
"""
import contextvars
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tickmark.config import settings

FORMAT = "%(asctime)s %(levelname)-5s %(step)-8s %(message)s"
job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("tickmark_job", default=None)
_captured: dict[str, list[dict]] = {}


def get(step: str) -> logging.Logger:
    return logging.getLogger(f"tickmark.{step}")


class _Step(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.step = record.name.removeprefix("tickmark.")
        return True


class _Capture(logging.Handler):
    """Keeps the records of the job running in this context (a thread sets job_id)."""

    def emit(self, record: logging.LogRecord) -> None:
        lines = _captured.get(job_id.get() or "")
        if lines is not None:
            lines.append({"t": self.formatter.formatTime(record, "%H:%M:%S"), "level": record.levelname,
                          "step": record.name.removeprefix("tickmark."), "msg": record.getMessage()})


def setup() -> None:
    """Console + rotating file + job capture, once."""
    root = logging.getLogger("tickmark")
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)  # the page can show details; console and file keep settings.log_level
    root.propagate = False
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(),
                RotatingFileHandler(Path(settings.log_dir) / "tickmark.log", maxBytes=5_000_000, backupCount=3),
                _Capture()]
    for h in handlers[:2]:
        h.setLevel(settings.log_level.upper())
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT, "%Y-%m-%d %H:%M:%S"))
        h.addFilter(_Step())
        root.addHandler(h)


def capture(job: str) -> list[dict]:
    """Start collecting this job's lines; the job's thread sets job_id to it."""
    return _captured.setdefault(job, [])
