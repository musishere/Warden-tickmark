"""The pipeline in one place: load -> clean -> read PDFs -> identify -> (vouch).

Used by the tools and by the audit store, so a stored run is exactly what the tools show.
With a Router as the extractor, PDFs are read locally first; those it cannot settle are read by
Azure (second pass), and the rows are matched and vouched again, once (tickmark.extract.router).
"""
from dataclasses import dataclass, field
from pathlib import Path

from tickmark.extract import Extractor
from tickmark.extract.router import Router
from tickmark.fields.mapping import ColumnMapping
from tickmark.match import identify
from tickmark.models import (
    CleanedSample,
    ExtractedInvoice,
    Identification,
    SampleLoadResult,
    Verdict,
)
from tickmark.sample import clean_sample, load_sample
from tickmark.vouch import VouchPolicy, vouch


@dataclass
class IdentificationRun:
    sample_path: Path
    invoice_files: list[Path]
    loaded: SampleLoadResult  # .definition: the catalogue plus this layout's own meanings
    cleaned: CleanedSample
    extracted: list[ExtractedInvoice]  # same order as invoice_files
    results: list[Identification]
    policy: VouchPolicy | None = None  # set when the claims were vouched
    verdicts: list[Verdict] = field(default_factory=list)  # one per result, same order
    routes: dict[str, tuple[str, str]] = field(default_factory=dict)  # file -> (read_by, why Azure)


def invoice_files(invoice_dir: Path) -> list[Path]:
    return sorted(Path(invoice_dir).glob("*.pdf"))


def run_identification(sample: Path, invoice_dir: Path, extractor: Extractor,
                       mapping: ColumnMapping | None = None) -> IdentificationRun:
    """mapping: which column holds which field (default: the header aliases)."""
    run = _identify(sample, invoice_dir, extractor, mapping)
    _second_pass(run, extractor)
    return run


def run_vouch(sample: Path, invoice_dir: Path, extractor: Extractor, policy: VouchPolicy,
              mapping: ColumnMapping | None = None) -> IdentificationRun:
    """Identify each row's PDF, then check whether it backs the row's claim."""
    run = _identify(sample, invoice_dir, extractor, mapping)
    run.policy = policy
    run.verdicts = vouch(run.results, run.cleaned.rows, run.extracted, policy, run.loaded.definition)
    _second_pass(run, extractor)
    return run


def _identify(sample: Path, invoice_dir: Path, extractor: Extractor, mapping: ColumnMapping | None):
    loaded = load_sample(sample, mapping=mapping)
    cleaned = clean_sample(loaded.rows, definition=loaded.definition)
    files = invoice_files(invoice_dir)
    if isinstance(extractor, Router):
        extractor.use(loaded.definition)
    extracted = [extractor.extract(p) for p in files]
    run = IdentificationRun(Path(sample), files, loaded, cleaned, extracted,
                            identify(cleaned.rows, extracted, loaded.definition))
    run.routes = extractor.routes if isinstance(extractor, Router) else {}
    return run


def _second_pass(run: IdentificationRun, extractor: Extractor) -> None:
    """Azure reads the PDFs local reading could not settle; then match and vouch again, once."""
    if isinstance(extractor, Router) and (want := extractor.second_pass(run)):
        paths = {p.name: p for p in run.invoice_files}
        run.extracted = [extractor.reread(paths[inv.file], inv, want[inv.file]) if inv.file in want else inv
                         for inv in run.extracted]
        run.results = identify(run.cleaned.rows, run.extracted, run.loaded.definition)
        if run.policy:
            run.verdicts = vouch(run.results, run.cleaned.rows, run.extracted, run.policy, run.loaded.definition)
    unread = [f"{inv.file} ({'; '.join(inv.needs_ocr)})" for inv in run.extracted
              if inv.needs_ocr and run.routes.get(inv.file, ("",))[0] == "local"]
    if unread:  # Azure is off: say which PDFs could not be looked at, where it matters
        note = f"not read (needs OCR; Azure is off or not available): {', '.join(unread)}"
        for r in run.results:
            if r.status != "linked":
                r.reason, r.steps = f"{r.reason}; {note}", [*r.steps, note]
        for v in run.verdicts:
            if v.verdict in ("no_evidence", "needs_review"):
                v.reasons.append(note)
