"""Record a human decision on one row of a stored run. Appends; never changes the tool's result.

    python -m tools.review RUN_ID ROW_ID --decision accept --tenant T --engagement E [--user ali]
    python -m tools.review RUN_ID H-03 --decision choose_candidate --document hard_03a.pdf \
        --note "client confirmed the April 1 invoice"      # with TICKMARK_TENANT / TICKMARK_ENGAGEMENT set

Decisions: accept | choose_candidate (needs --document) | reject | requested_from_client
"""
import argparse

from tickmark.store import DECISIONS, add_review
from tools.tenant_args import add_tenant_args, tenant_context


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("row_id", help="the sample row id, e.g. S-01")
    ap.add_argument("--decision", required=True, choices=DECISIONS)
    ap.add_argument("--document", help="the PDF chosen (for choose_candidate), as named in the run")
    ap.add_argument("--note", default="")
    add_tenant_args(ap)
    args = ap.parse_args()
    ctx = tenant_context(ap, args)
    try:
        review_id = add_review(ctx, args.run_id, args.row_id, decision=args.decision,
                               chosen_file=args.document, note=args.note)
    except KeyError as e:
        raise SystemExit(str(e)) from None
    print(f"recorded review {review_id}: {ctx.user} {args.decision} row {args.row_id}"
          + (f" → {args.document}" if args.document else ""))


if __name__ == "__main__":
    main()
