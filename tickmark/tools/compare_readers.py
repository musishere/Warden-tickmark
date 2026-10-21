"""Local reading vs Azure on the same PDFs: is local reading safe to trust where it is confident?

    python -m tools.compare_readers INVOICE_DIR          # needs TICKMARK_DOCINT_* (results are cached)

For every PDF: which file check sent it to Azure (needs_ocr), and per field whether local read it,
Azure read it, and whether they agree. The number that matters is "local confident, Azure
disagrees": it must be 0 before local reading is relied on (exit 1 otherwise). Tune the
TICKMARK_PDF_* settings and the definition's labels until it is.
"""
import argparse
import sys
from pathlib import Path

from tickmark.extract import DocIntExtractor
from tickmark.extract.local import LocalExtractor
from tickmark.extract.router import _same
from tickmark.pipeline import invoice_files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("invoices", type=Path)
    args = ap.parse_args()
    local_reader, azure_reader = LocalExtractor(), DocIntExtractor()
    disagree = local_only = azure_only = agree = scans = 0
    for f in invoice_files(args.invoices):
        local, azure = local_reader.extract(f), azure_reader.extract(f)
        if local.needs_ocr:
            scans += 1
            print(f"{f.name}: Azure needed ({'; '.join(local.needs_ocr)})")
            continue
        for name in sorted(set(local.fields) | set(azure.fields)):
            lv, av = local.get(name), azure.get(name)
            if lv and av and not _same(lv.value, av.value):
                disagree += 1
                print(f"❌ {f.name} {name}: local {lv.value!r}, Azure {av.value!r} (confidence {av.confidence})")
            elif lv and av:
                agree += 1
            elif lv:
                local_only += 1
            else:
                azure_only += 1
                why = local.unread.get(name, "no label found")
                print(f"·  {f.name} {name}: only Azure read it ({av.value!r}); local: {why}")
    print(f"\nfields: {agree} agree | {disagree} local confident, Azure disagrees | {local_only} only local | "
          f"{azure_only} only Azure | {scans} PDFs need OCR")
    print("✅ local reading never contradicted Azure" if not disagree else "❌ local reading is not safe yet")
    sys.exit(1 if disagree else 0)


if __name__ == "__main__":
    main()
