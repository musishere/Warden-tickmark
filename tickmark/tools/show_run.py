"""Show a stored run, rebuilt only from the audit database (no PDFs or Excel files are re-read).

    python -m tools.show_run                       # list recent runs
    python -m tools.show_run RUN_ID [--trace] [--out data/output/run.xlsx]

The output is the same as `tools.identify_sample --trace` at the time of the run, plus the human
reviews. Workbook links open the stored evidence copies, so they show exactly what the run used.
"""
import argparse
from pathlib import Path

from tickmark.store import get_run, list_runs
from tools.identify_sample import print_results, write_workbook


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    if not args.run_id:
        for r in list_runs():
            print(f"{r['id']}  {r['started_at']:%Y-%m-%d %H:%M}  {r['status']:<9} {r['run_by']:<10} "
                  f"{r['engagement_id'] or '—':<10} {r['extractor']:<12} {r['matcher_version']}")
        return

    s = get_run(args.run_id)
    run = s.run
    print(f"Run {run['id']}  ({run['status']})")
    print(f"  started {run['started_at']:%Y-%m-%d %H:%M:%S %Z} by {run['run_by']}, engagement {run['engagement_id'] or '—'}")
    print(f"  matcher {run['matcher_version']}, thresholds {run['thresholds']}")
    print(f"  extractor {run['extractor']} {run['model_id'] or ''}")
    if run["error"]:
        print(f"  ERROR: {run['error']}")
    if s.sample:
        print(f"  sample sheet {run['sheet']!r}, sha256 {s.sample['sha256']}")
    print(f"  {len(s.files)} PDFs, {len(s.results)} rows, {len(s.issues)} row issue(s)")
    for i in s.issues:
        print(f"    Excel row {i['excel_row']}: {i['kind']}: {i['reason']}")
    print()
    print_results(s.results, s.extracted, args.trace, s.reviews)
    if args.out:
        write_workbook(args.out, s.results, s.extracted, {f["file_name"]: Path(f["storage_path"]) for f in s.files},
                       s.reviews)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
