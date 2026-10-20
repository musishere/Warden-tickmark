"""Prove that loading + cleaning lost no rows and changed no values. Needs no labels.

    python -m tools.verify_clean data/generated/sample_messy.xlsx [data/output/sample_messy_cleaned.xlsx]

Checks, each against the raw Excel cells rather than against the loader's own logic:
  1. completeness   every non-blank row is loaded, an error, or a skipped footer: exactly once
  2. fidelity       each loaded value is the raw value: vendor differs only in whitespace,
                    amount has the same digits and sign, date and row id are the same
  3. untouched      clean_sample returns every loaded row unmodified, in order
  4. control totals count and amount sum of loaded rows match the raw cells
  5. round trip     the cleaned workbook (if given) holds exactly the loaded rows and issues
Exits 1 if any check fails.
"""
import argparse
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from tickmark.sample import clean_sample
from tickmark.sample.loader import _map_headers, load_sample

INVISIBLE = re.compile(r"[\s​‌‍﻿]+")


def _blank(values: list) -> bool:
    return all(v in (None, "") or (isinstance(v, str) and not v.strip()) for v in values)


def _as_date(v) -> date | None:
    return v.date() if isinstance(v, datetime) else v if isinstance(v, date) else None


def _same_amount(raw, amount: Decimal) -> bool:
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        return Decimal(str(raw)) == amount
    text = str(raw).strip()
    negative = "-" in text or (text.startswith("(") and text.endswith(")"))
    # same digits in the same order, and the decimals are whatever follows a final [.,] + 1-2 digits
    tail = re.search(r"[.,](\d{1,2})\D*$", text)
    decimals = len(tail[1]) if tail else 0
    return (re.sub(r"\D", "", text).lstrip("0") == re.sub(r"\D", "", f"{abs(amount):f}").lstrip("0")
            and -amount.as_tuple().exponent == decimals and negative == (amount < 0))


def _same_date(raw, d: date) -> bool:
    if (rd := _as_date(raw)) is not None:
        return rd == d
    nums = [int(n) for n in re.findall(r"\d+", str(raw))]
    return sorted(nums) == sorted([d.year, d.month, d.day]) and len(nums) == 3


def verify(src: Path, dst: Path | None = None) -> list[str]:
    problems: list[str] = []
    ws = load_workbook(src, data_only=True).worksheets[0]
    raw = {r[0].row: [c.value for c in r] for r in ws.iter_rows()}
    header = next(n for n, v in raw.items() if not _blank(v))
    cols = _map_headers(raw[header])
    data_rows = {n for n, v in raw.items() if n > header and not _blank(v)}

    loaded = load_sample(src)
    buckets = ([r.excel_row for r in loaded.rows], [e.row_number for e in loaded.errors],
               [s.row_number for s in loaded.skipped])
    seen = [n for b in buckets for n in b]

    # 1. completeness
    for n in sorted(data_rows - set(seen)):
        problems.append(f"row {n}: LOST (not loaded, not an error, not skipped)")
    for n in sorted(set(seen) - data_rows):
        problems.append(f"row {n}: appears in output but is blank or not in the sheet")
    for n in sorted({n for n in seen if seen.count(n) > 1}):
        problems.append(f"row {n}: accounted for {seen.count(n)} times")

    # 2. fidelity
    for r in loaded.rows:
        cells = raw[r.excel_row]
        cell = lambda f: cells[cols[f]] if f in cols and cols[f] < len(cells) else None  # noqa: E731
        if INVISIBLE.sub("", str(cell("vendor"))) != INVISIBLE.sub("", r.vendor):
            problems.append(f"row {r.excel_row}: vendor changed {cell('vendor')!r} -> {r.vendor!r}")
        if not _same_amount(cell("amount"), r.amount):
            problems.append(f"row {r.excel_row}: amount changed {cell('amount')!r} -> {r.amount}")
        if not _same_date(cell("invoice_date"), r.invoice_date):
            problems.append(f"row {r.excel_row}: date changed {cell('invoice_date')!r} -> {r.invoice_date}")
        if "row_id" in cols and cell("row_id") not in (None, "") and str(cell("row_id")) != str(r.row_id):
            problems.append(f"row {r.excel_row}: row id changed {cell('row_id')!r} -> {r.row_id}")

    # 3. untouched by the cleaner
    before = [r.model_dump() for r in loaded.rows]
    cleaned = clean_sample(loaded.rows)
    if [cr.row.model_dump() for cr in cleaned.rows] != before:
        problems.append("clean_sample changed, dropped or reordered loaded rows")
    if [r.model_dump() for r in loaded.rows] != before:
        problems.append("clean_sample mutated its input")

    # 4. control totals
    raw_sum = sum((Decimal(str(raw[n][cols["amount"]])) for n in buckets[0]
                   if isinstance(raw[n][cols["amount"]], (int, float))), Decimal(0))
    out_sum = sum((cr.row.amount for cr in cleaned.rows
                   if isinstance(raw[cr.row.excel_row][cols["amount"]], (int, float))), Decimal(0))
    if raw_sum != out_sum:
        problems.append(f"control total of numeric amounts: raw {raw_sum} != cleaned {out_sum}")

    # 5. round trip through the written workbook
    if dst:
        wb = load_workbook(dst, data_only=True)
        out = {v[0]: v for v in wb["Clean"].iter_rows(min_row=2, values_only=True)}
        issues = {v[0] for v in wb["Issues"].iter_rows(min_row=2, values_only=True)}
        want = {cr.row.excel_row: cr for cr in cleaned.rows}
        if set(out) != set(want):
            problems.append(f"Clean sheet rows {sorted(out)} != loaded rows {sorted(want)}")
        if issues != set(buckets[1] + buckets[2]):
            problems.append(f"Issues sheet rows {sorted(issues)} != errors+skipped {sorted(buckets[1] + buckets[2])}")
        for n in set(out) & set(want):
            _, rid, vendor, _, amount, d, _ = out[n]
            r = want[n].row
            if (rid, vendor, Decimal(str(amount)), _as_date(d)) != (r.row_id, r.vendor, r.amount, r.invoice_date):
                problems.append(f"row {n}: written {out[n][1:6]} != loaded "
                                f"{(r.row_id, r.vendor, r.amount, r.invoice_date)}")

    print(f"{src}: {len(data_rows)} data rows = {len(buckets[0])} loaded + {len(buckets[1])} errors "
          f"+ {len(buckets[2])} skipped | numeric amount total {raw_sum} -> {out_sum}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path, nargs="?")
    args = ap.parse_args()
    problems = verify(args.src, args.dst)
    for p in problems:
        print(f"❌ {p}")
    print("✅ nothing lost, nothing changed" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
