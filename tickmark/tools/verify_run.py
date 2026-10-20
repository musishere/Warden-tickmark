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
    sample = get_run(ctx, args.run_id).sample.get("storage_path")
    if sample and Path(sample).is_file():
        problems += [f"sample: {p}" for p in verify_clean(Path(sample))]
    for p in problems:
        print(f"❌ {p}")
    print("✅ run verified: evidence unchanged, every row accounted for" if not problems
          else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
