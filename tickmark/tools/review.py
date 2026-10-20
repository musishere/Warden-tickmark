"""Record a human decision on one row of a stored run. Appends; never changes the tool's result.

    python -m tools.review RUN_ID ROW_ID --decision accept --reviewer ali
    python -m tools.review RUN_ID H-03 --decision choose_candidate --document hard_03a.pdf --reviewer ali \
        --note "client confirmed the April 1 invoice"

Decisions: accept | choose_candidate (needs --document) | reject | requested_from_client
"""
import argparse
import getpass

from tickmark.store import DECISIONS, add_review


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("row_id", help="the sample row id, e.g. S-01")
    ap.add_argument("--decision", required=True, choices=DECISIONS)
    ap.add_argument("--document", help="the PDF chosen (for choose_candidate), as named in the run")
    ap.add_argument("--reviewer", default=getpass.getuser())
    ap.add_argument("--note", default="")
    args = ap.parse_args()
    review_id = add_review(args.run_id, args.row_id, reviewer=args.reviewer, decision=args.decision,
                           chosen_file=args.document, note=args.note)
    print(f"recorded review {review_id}: {args.reviewer} {args.decision} row {args.row_id}"
          + (f" → {args.document}" if args.document else ""))


if __name__ == "__main__":
    main()
