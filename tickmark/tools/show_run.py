"""Show a stored run, rebuilt only from the audit database (no PDFs or Excel files are re-read).

    python -m tools.show_run --tenant T --engagement E                 # list that engagement's runs
    python -m tools.show_run RUN_ID --tenant T --engagement E [--trace] [--out data/output/run.xlsx]

A run of another firm or engagement is reported as "no run": its existence is not revealed.

The output is the same as `tools.identify_sample --trace` at the time of the run, plus the human
reviews. Workbook links open the stored evidence copies, so they show exactly what the run used.
"""
import argparse
from pathlib import Path

from tickmark.store import get_run, list_runs
from tools.identify_sample import print_results, write_workbook
from tools.tenant_args import add_tenant_args, tenant_context


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--out", type=Path)
    add_tenant_args(ap)
    args = ap.parse_args()
    ctx = tenant_context(ap, args)

    if not args.run_id:
        for r in list_runs(ctx):
            print(f"{r['id']}  {r['started_at']:%Y-%m-%d %H:%M}  {r['status']:<9} {r['run_by']:<10} "
                  f"{r['extractor']:<12} {r['matcher_version']}")
        return

    try:
        s = get_run(ctx, args.run_id)
    except KeyError:
        raise SystemExit(f"no run {args.run_id} in this engagement")
    run = s.run
    print(f"Run {run['id']}  ({run['status']})")
    print(f"  tenant {run['tenant_id']}, engagement {run['engagement_id']}")
    print(f"  started {run['started_at']:%Y-%m-%d %H:%M:%S %Z} by {run['run_by']}")
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
