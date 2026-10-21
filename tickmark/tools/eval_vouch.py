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
from pathlib import Path

from tickmark.pipeline import run_vouch
from tickmark.vouch import MARK, VouchPolicy, check_invoice, duplicate_map, parse_rates
from tools.eval_identify import EXT, GEN, HARD
from tools.reader_args import add_reader_args, make_reader, print_routes

Y2025, Y2026 = (date(2025, 1, 1), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 12, 31))
CHECK_DEFECT = {"lines_sum": "lines_dont_sum", "vat_rate": "vat_wrong", "in_period": "date_out_of_period",
                "duplicate": "duplicate_number", "totals_reconcile": "totals_dont_reconcile"}


def truth_generated(line_items: bool = True) -> dict[str, str]:
    """Labels say whether the row differs from its invoice; an invoice defect also makes it ⚠️ -
    item lines that do not add up only when that check is on."""
    defects = {p.stem + ".pdf": [d for d in json.loads(p.read_text())["defects"] if line_items or d != "lines_dont_sum"]
               for p in (GEN / "labels").glob("invoice_*.json")}
    out = {}
    for s in json.loads((GEN / "labels" / "sample_labels.json").read_text()):
        if s["expected"] == "no_evidence":
            out[str(s["row_id"])] = "no_evidence"
        else:
            out[str(s["row_id"])] = "difference" if s["expected"] == "difference" or defects[s["invoice_file"]] \
                else "matched"
    return out


def truth_external(line_items: bool = True) -> dict[str, str]:
    return {e["row_id"]: e["expected_status"] for e in json.loads((EXT / "expected_results.json").read_text())}


def truth_hard(line_items: bool = True) -> dict[str, str]:
    return {t["row_id"]: {"link": "matched", "review": "needs_review", "none": "no_evidence"}[t["expect"]]
            for t in json.loads((HARD / "truth.json").read_text())}


# checked by hand against the visible invoice: <dir>/truth.json holds the period, currency, VAT rates and rows
TRUTHED = {"real": Path("data/real/facturx"), "variety-originals": Path("data/real/variety/originals"),
           "variety-scans": Path("data/real/variety/scans"), "variety-photos": Path("data/real/variety/photos")}


def _truth(base: Path) -> dict:
    return json.loads((base / "truth.json").read_text()) if (base / "truth.json").exists() else {}


def _truthed(base: Path):
    def rows(line_items: bool = True) -> dict[str, str | list[str]]:
        """A row may accept more than one verdict (a list), e.g. a wrong PDF: ⚠️ or ❓."""
        return {r: t["expect"] for r, t in _truth(base)["rows"].items()}
    return rows


def _truthed_policy(base: Path) -> dict:
    if not (t := _truth(base)):
        return {}
    start, end = (date.fromisoformat(d) for d in t["period"].split(":"))
    return {"period_start": start, "period_end": end, "vat_rates": parse_rates(t["vat_rates"]),
            "currency": t["currency"]}


DATASETS = {"generated": (GEN, Y2025, truth_generated), "external": (EXT, Y2026, truth_external),
            "hard": (HARD, Y2025, truth_hard),
            **{name: (base, (None, None), _truthed(base)) for name, base in TRUTHED.items()}}


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


def evaluate(name: str, use_labels: bool, azure: str = "auto", line_items: bool = True) -> int:
    base, period, truth_fn = DATASETS[name]
    if not (base / "sample.xlsx").exists():
        print(f"{name}: skipped ({base} missing)\n")
        return 0
    extractor = make_reader(base / "labels" if use_labels else None, azure)
    run = run_vouch(base / "sample.xlsx", base / "invoices", extractor,
                    VouchPolicy(**{"period_start": period[0], "period_end": period[1],
                                   "currency": dataset_currency(base), "line_items": line_items,
                                   **(_truthed_policy(base) if base in TRUTHED.values() else {})}))
    truth = truth_fn(line_items)
    right = false_ok = 0
    print(f"== {name} ({len(run.verdicts)} rows)")
    for v in run.verdicts:
        want = truth.get(v.row_id, "no_evidence")
        ok = v.verdict in want if isinstance(want, list) else v.verdict == want
        want = want[0] if isinstance(want, list) else want
        right += ok
        bad = v.verdict == "matched" and want != "matched"
        false_ok += bad
        if not ok:
            print(f"   {'❌ FALSE ✅' if bad else '·  differs'} {v.row_id}: got {MARK[v.verdict]} {v.verdict}, "
                  f"expected {MARK[want]} {want} | {'; '.join(v.reasons)[:120]}")
    for key, want in truth.items():  # a stray page (no invoice of its own) must back no row
        if want == "unlinked" and (rows := [v.row_id for v in run.verdicts if v.invoice_file == key.split()[-1]]):
            false_ok += 1
            print(f"   ❌ WRONG LINK {key} backs row(s) {', '.join(rows)}")
    counts = " | ".join(f"{MARK[k]} {sum(v.verdict == k for v in run.verdicts)}" for k in MARK)
    print(f"   verdicts {right}/{len(run.verdicts)} as labelled | false ✅ {false_ok} | {counts}")
    print_routes(run)

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
    ap.add_argument("--labels", action="store_true", help="extract from label JSON (perfect extraction)")
    ap.add_argument("--line-items", action=argparse.BooleanOptionalAction, default=True,
                    help="check item lines (default on here, to measure every check)")
    add_reader_args(ap)
    ap.add_argument("datasets", nargs="*", choices=list(DATASETS), help="default: all")
    args = ap.parse_args()
    print(f"extraction: {'labels (perfect, tests the rules only)' if args.labels else f'own text, Azure {args.azure}'}"
          f" | item lines {'on' if args.line_items else 'off'}\n")
    false_ok = sum(evaluate(d, args.labels, args.azure, args.line_items) for d in args.datasets or DATASETS)
    print("✅ no false ✅" if not false_ok else f"❌ {false_ok} false ✅")
    sys.exit(1 if false_ok else 0)


if __name__ == "__main__":
    main()
