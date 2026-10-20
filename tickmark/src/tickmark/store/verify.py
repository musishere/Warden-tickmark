"""Check a stored run against its evidence: files unchanged, extraction unchanged, no row missing."""
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, select

from tickmark.sample import load_sample
from tickmark.store.context import TenantContext, scoped
from tickmark.store.evidence import sha256_file
from tickmark.store.queries import get_run
from tickmark.store.record import canonical_sha256
from tickmark.store.schema import extractions, sample_rows


def verify_run(ctx: TenantContext, run_id: UUID | str, db: Engine | None = None) -> list[str]:
    stored = get_run(ctx, run_id, db)
    run_id = stored.run["id"]
    problems: list[str] = []

    # 1. every file still has the bytes the run used
    files = [("sample", stored.sample.get("sha256"), stored.sample.get("storage_path"))]
    files += [(f["file_name"], f["sha256"], f["storage_path"]) for f in stored.files]
    for name, sha, path in files:
        if not path or not Path(path).is_file():
            problems.append(f"{name}: stored file missing ({path})")
        elif sha256_file(Path(path)) != sha:
            problems.append(f"{name}: stored file changed (sha256 no longer {sha[:12]}…)")

    with scoped(ctx, db) as conn:
        # 2. what the extractor returned is unchanged
        ids = [f["extraction_id"] for f in stored.files]
        for e in conn.execute(select(extractions.c.id, extractions.c.raw, extractions.c.raw_sha256)
                              .where(extractions.c.id.in_(ids))).mappings():
            if canonical_sha256(e["raw"]) != e["raw_sha256"]:
                problems.append(f"extraction {e['id']}: raw result does not match its hash")

        # 3. the stored rows are exactly what the stored sample loads to: nothing lost, nothing changed
        db_rows = {r["excel_row"]: r for r in conn.execute(
            select(sample_rows).where(sample_rows.c.run_id == run_id)).mappings()}
    sample_path = stored.sample.get("storage_path")
    if sample_path and Path(sample_path).is_file():
        loaded = load_sample(sample_path)
        want_rows = {r.excel_row: r for r in loaded.rows}
        want_issues = {e.row_number for e in loaded.errors} | {s.row_number for s in loaded.skipped}
        got_issues = {i["excel_row"] for i in stored.issues}
        for n in sorted(set(want_rows) ^ set(db_rows)):
            problems.append(f"Excel row {n}: {'missing from' if n in want_rows else 'unexpected in'} sample_rows")
        for n in sorted(want_issues ^ got_issues):
            problems.append(f"Excel row {n}: {'missing from' if n in want_issues else 'unexpected in'} sample_issues")
        for n in sorted(set(want_rows) & set(db_rows)):
            w, g = want_rows[n], db_rows[n]
            if (w.row_id, w.vendor, w.amount, w.invoice_date, w.reference) != (
                    g["row_id"], g["vendor"], g["amount"], g["invoice_date"], g["reference"]):
                problems.append(f"Excel row {n}: stored values differ from the sample")
        results_rows = {r.excel_row for r in stored.results}
        for n in sorted(set(db_rows) - results_rows):
            problems.append(f"Excel row {n}: has no match result")
    return problems
