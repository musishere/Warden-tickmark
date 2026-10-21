"""Measure the verdicts (✅ / ⚠️ / 👀 / ❓) against the labels, and the invoice checks against the
labelled defects.

    python -m tools.eval_vouch --labels   # perfect extraction from labels: tests the rules alone
    python -m tools.eval_vouch            # with Azure Document Intelligence (cached)

The dangerous error is a false ✅ (a row passed although the labels say difference or no evidence):
the run exits 1 if there is any.
"""
import argparse
import json
import sys
from datetime import date

from tickmark.extract import DocIntExtractor, LabelExtractor
from tickmark.pipeline import run_vouch
from tickmark.vouch import MARK, VouchPolicy, check_invoice, duplicate_map
from tools.eval_identify import EXT, GEN, HARD

Y2025, Y2026 = (date(2025, 1, 1), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 12, 31))
CHECK_DEFECT = {"lines_sum": "lines_dont_sum", "vat_rate": "vat_wrong", "in_period": "date_out_of_period",
                "duplicate": "duplicate_number", "totals_reconcile": "totals_dont_reconcile"}


def truth_generated() -> dict[str, str]:
    """Labels say whether the row differs from its invoice; an invoice defect also makes it ⚠️."""
    defects = {p.stem + ".pdf": json.loads(p.read_text())["defects"] for p in (GEN / "labels").glob("invoice_*.json")}
    out = {}
    for s in json.loads((GEN / "labels" / "sample_labels.json").read_text()):
        if s["expected"] == "no_evidence":
            out[str(s["row_id"])] = "no_evidence"
        else:
            out[str(s["row_id"])] = "difference" if s["expected"] == "difference" or defects[s["invoice_file"]] \
                else "matched"
    return out


def truth_external() -> dict[str, str]:
    return {e["row_id"]: e["expected_status"] for e in json.loads((EXT / "expected_results.json").read_text())}


def truth_hard() -> dict[str, str]:
    return {t["row_id"]: {"link": "matched", "review": "needs_review", "none": "no_evidence"}[t["expect"]]
            for t in json.loads((HARD / "truth.json").read_text())}


DATASETS = {"generated": (GEN, Y2025, truth_generated), "external": (EXT, Y2026, truth_external),
            "hard": (HARD, Y2025, truth_hard)}


def dataset_currency(base) -> str | None:
    """The one currency every invoice label of a dataset is in (rows state none), else None."""
    found = set()
    for f in (base / "labels").glob("*.json"):
        label = json.loads(f.read_text())
        if isinstance(label, dict):  # sample_labels.json is a list of rows, not an invoice
            found.add((label.get("invoice") or label).get("currency"))
    return found.pop() if len(found) == 1 else None


def defect_truth(run) -> dict[str, set[str]]:
    """Generated invoice labels; a duplicate number involves every invoice that carries it."""
    labels = {p.stem + ".pdf": json.loads(p.read_text()) for p in (GEN / "labels").glob("invoice_*.json")}
    reused = {lab["invoice"]["invoice_number"] for lab in labels.values() if "duplicate_number" in lab["defects"]}
    return {f: set(lab["defects"]) | ({"duplicate_number"} if lab["invoice"]["invoice_number"] in reused else set())
            for f, lab in labels.items()}


def evaluate(name: str, use_labels: bool) -> int:
    base, period, truth_fn = DATASETS[name]
    if not (base / "sample.xlsx").exists():
        print(f"{name}: skipped ({base} missing)\n")
        return 0
    extractor = LabelExtractor(base / "labels") if use_labels else DocIntExtractor()
    run = run_vouch(base / "sample.xlsx", base / "invoices", extractor,
                    VouchPolicy(period_start=period[0], period_end=period[1], currency=dataset_currency(base)))
    truth = truth_fn()
    right = false_ok = 0
    print(f"== {name} ({len(run.verdicts)} rows)")
    for v in run.verdicts:
        want = truth.get(v.row_id, "no_evidence")
        right += v.verdict == want
        bad = v.verdict == "matched" and want != "matched"
        false_ok += bad
        if v.verdict != want:
            print(f"   {'❌ FALSE ✅' if bad else '·  differs'} {v.row_id}: got {MARK[v.verdict]} {v.verdict}, "
                  f"expected {MARK[want]} {want} | {'; '.join(v.reasons)[:120]}")
    counts = " | ".join(f"{MARK[k]} {sum(v.verdict == k for v in run.verdicts)}" for k in MARK)
    print(f"   verdicts {right}/{len(run.verdicts)} as labelled | false ✅ {false_ok} | {counts}")

    if name == "generated":  # every PDF, linked or not: do the checks find the labelled defects?
        want_by_file, dups = defect_truth(run), duplicate_map(run.extracted)
        stats = {d: [0, 0, 0] for d in CHECK_DEFECT.values()}  # found right, missed, false alarm
        for inv in run.extracted:
            found = {CHECK_DEFECT[c.name] for c in check_invoice(inv, run.policy, dups) if c.status in ("fail", "warn")}
            want = want_by_file.get(inv.file, set())
            for d, counts in stats.items():
                counts[0] += d in found and d in want
                counts[1] += d in want and d not in found
                counts[2] += d in found and d not in want
        print("   invoice checks over all PDFs (found / missed / false alarm):")
        for d, (ok, missed, fa) in stats.items():
            print(f"     {d:<22} {ok} / {missed} / {fa}")
    print()
    return false_ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", action="store_true", help="extract from label JSON instead of Azure")
    ap.add_argument("datasets", nargs="*", choices=list(DATASETS), help="default: all")
    args = ap.parse_args()
    print(f"extraction: {'labels (perfect, tests the rules only)' if args.labels else 'Azure Document Intelligence'}\n")
    false_ok = sum(evaluate(d, args.labels) for d in args.datasets or DATASETS)
    print("✅ no false ✅" if not false_ok else f"❌ {false_ok} false ✅")
    sys.exit(1 if false_ok else 0)


if __name__ == "__main__":
    main()
