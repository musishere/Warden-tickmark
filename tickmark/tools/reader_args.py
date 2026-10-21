"""--azure for the tools, and the one rule for how a run reads its PDFs: label JSON when --labels
is given (testing only), else each PDF's own text first and Azure only where needed
(tickmark.extract.router; --azure auto|off|always, default TICKMARK_AZURE)."""
import argparse
from pathlib import Path

from tickmark.config import settings
from tickmark.extract import LabelExtractor
from tickmark.extract.router import Router


def add_reader_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--azure", choices=("auto", "off", "always"), default=settings.azure,
                    help="auto: Azure only for PDFs that cannot be read from their own text (default); "
                         "off: never; always: every PDF")


def make_reader(labels: Path | None, azure: str = settings.azure):
    return LabelExtractor(labels) if labels else Router(azure)


def print_routes(run) -> None:
    if not run.routes:
        return
    azure = {f: why for f, (by, why) in run.routes.items() if by == "azure"}
    missed = {f: why for f, (by, why) in run.routes.items() if by == "local" and why.startswith("Azure needed")}
    print(f"\nPDFs: {len(run.routes) - len(azure)} read from their own text, {len(azure)} by Azure"
          + "".join(f"\n  {f}: {why}" for f, why in sorted((azure | missed).items())))
    unread = [inv.file for inv in run.extracted if inv.needs_ocr and run.routes.get(inv.file, ("",))[0] == "local"]
    if unread:
        print(f"  not read (needs OCR; Azure is off or not available): {', '.join(unread)}")
