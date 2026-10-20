"""Check a stored run against its evidence. Exits 1 on any problem.

    python -m tools.verify_run RUN_ID --tenant T --engagement E

1. every stored file (sample + PDFs) still has the SHA-256 recorded for the run
2. every stored extraction still matches its hash
3. the stored sample loads to exactly the stored rows + issues, and every row has a result
4. the stored sample passes the "nothing lost, nothing changed" check against its raw cells
"""
import argparse
import sys
from pathlib import Path

from tickmark.fields.mapping import ColumnMapping
from tickmark.store import get_run, verify_run
from tools.tenant_args import add_tenant_args, tenant_context
from tools.verify_clean import verify as verify_clean


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    add_tenant_args(ap)
    args = ap.parse_args()
    ctx = tenant_context(ap, args)
    try:
        problems = verify_run(ctx, args.run_id)
    except KeyError:
        raise SystemExit(f"no run {args.run_id} in this engagement") from None
    stored = get_run(ctx, args.run_id)
    sample = stored.sample.get("storage_path")
    mapping = None
    if stored.mapping:  # the run's own column mapping, so the check reads the same columns the run did
        m = stored.mapping
        mapping = ColumnMapping(definition=m["definition"], version=m["definition_version"], columns=m["columns"],
                                source=m["source"], confirmed_by=m["confirmed_by"], headers_sha256=m["headers_sha256"])
    if sample and Path(sample).is_file():
        problems += [f"sample: {p}" for p in verify_clean(Path(sample), mapping=mapping)]
    for p in problems:
        print(f"❌ {p}")
    print("✅ run verified: evidence unchanged, every row accounted for" if not problems
          else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
