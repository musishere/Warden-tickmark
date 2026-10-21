"""How well does reading a PDF's own text work? Local reading vs the known truth, field by field.

    python -m tools.eval_reader data/real/facturx      # any folder with invoices/ and labels/

Per field: read and right, read and WRONG (the dangerous one: local reading is trusted), not read
(goes to Azure, or 👀). Per PDF: whether the file check sent it to OCR. Exit 1 if anything was read
wrong. No Azure, no cost.
"""
import argparse
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tickmark.extract import LabelExtractor
from tickmark.extract.local import LocalExtractor
from tickmark.pipeline import invoice_files

FIELDS = ("invoice_number", "invoice_date", "total", "subtotal", "tax", "currency")


def same(a: str, b: str) -> bool:
    try:
        return Decimal(a) == Decimal(b)
    except InvalidOperation:
        return a.strip().casefold() == b.strip().casefold()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("case", type=Path)
    ap.add_argument("--show", type=int, default=15, help="how many unread examples to print per field")
    args = ap.parse_args()
    reader, truth_reader = LocalExtractor(), LabelExtractor(args.case / "labels")
    right, wrong, unread, ocr = Counter(), Counter(), Counter(), []
    unread_examples: dict[str, list[str]] = {}
    files = invoice_files(args.case / "invoices")
    for f in files:
        local, truth = reader.extract(f), truth_reader.extract(f)
        if local.needs_ocr:
            ocr.append(f"{f.name}: {'; '.join(local.needs_ocr)}")
            continue
        for name in FIELDS:
            t, v = truth.get(name), local.get(name)
            if t is None:
                continue
            if v is None:
                unread[name] += 1
                unread_examples.setdefault(name, []).append(f"{f.name}: truth {t.value!r}; {local.unread.get(name, 'no label found')}")
            elif same(v.value, t.value):
                right[name] += 1
            else:
                wrong[name] += 1
                print(f"❌ {f.name} {name}: read {v.value!r} from {v.snip.text!r} (page {v.snip.page}), truth {t.value!r}")
    print(f"\n{len(files)} PDFs, {len(ocr)} need OCR (Azure)")
    for line in ocr:
        print(f"   {line}")
    print(f"\n{'field':<15} {'right':>6} {'WRONG':>6} {'not read':>9}")
    for name in FIELDS:
        print(f"{name:<15} {right[name]:>6} {wrong[name]:>6} {unread[name]:>9}")
    for name, ex in unread_examples.items():
        print(f"\nnot read: {name} (first {args.show})")
        for line in ex[:args.show]:
            print(f"   {line}")
    print("\n✅ local reading never contradicted the truth" if not sum(wrong.values())
          else f"\n❌ {sum(wrong.values())} values read wrong")
    sys.exit(1 if sum(wrong.values()) else 0)


if __name__ == "__main__":
    main()
