"""The pipeline in one place: load -> clean -> read PDFs -> identify -> (vouch).

Used by the tools and by the audit store, so a stored run is exactly what the tools show.
"""
from dataclasses import dataclass, field
from pathlib import Path

from tickmark.extract import Extractor
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
    loaded: SampleLoadResult
    cleaned: CleanedSample
    extracted: list[ExtractedInvoice]  # same order as invoice_files
    results: list[Identification]
    policy: VouchPolicy | None = None  # set when the claims were vouched
    verdicts: list[Verdict] = field(default_factory=list)  # one per result, same order


def invoice_files(invoice_dir: Path) -> list[Path]:
    return sorted(Path(invoice_dir).glob("*.pdf"))


def run_identification(sample: Path, invoice_dir: Path, extractor: Extractor,
                       mapping: ColumnMapping | None = None) -> IdentificationRun:
    """mapping: which column holds which field (default: the header aliases)."""
    loaded = load_sample(sample, mapping=mapping)
    cleaned = clean_sample(loaded.rows)
    files = invoice_files(invoice_dir)
    extracted = [extractor.extract(p) for p in files]
    return IdentificationRun(Path(sample), files, loaded, cleaned, extracted,
                             identify(cleaned.rows, extracted))


def run_vouch(sample: Path, invoice_dir: Path, extractor: Extractor, policy: VouchPolicy,
              mapping: ColumnMapping | None = None) -> IdentificationRun:
    """Identify each row's PDF, then check whether it backs the row's claim."""
    run = run_identification(sample, invoice_dir, extractor, mapping)
    run.policy = policy
    run.verdicts = vouch(run.results, run.cleaned.rows, run.extracted, policy)
    return run
