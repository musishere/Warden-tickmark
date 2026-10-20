"""The identification pipeline in one place: load -> clean -> read PDFs -> identify.

Used by the tools and by the audit store, so a stored run is exactly what the tools show.
"""
from dataclasses import dataclass
from pathlib import Path

from tickmark.extract import Extractor
from tickmark.match import identify
from tickmark.models import CleanedSample, ExtractedInvoice, Identification, SampleLoadResult
from tickmark.sample import clean_sample, load_sample


@dataclass
class IdentificationRun:
    sample_path: Path
    invoice_files: list[Path]
    loaded: SampleLoadResult
    cleaned: CleanedSample
    extracted: list[ExtractedInvoice]  # same order as invoice_files
    results: list[Identification]


def invoice_files(invoice_dir: Path) -> list[Path]:
    return sorted(Path(invoice_dir).glob("*.pdf"))


def run_identification(sample: Path, invoice_dir: Path, extractor: Extractor) -> IdentificationRun:
    loaded = load_sample(sample)
    cleaned = clean_sample(loaded.rows)
    files = invoice_files(invoice_dir)
    extracted = [extractor.extract(p) for p in files]
    return IdentificationRun(Path(sample), files, loaded, cleaned, extracted,
                             identify(cleaned.rows, extracted))
