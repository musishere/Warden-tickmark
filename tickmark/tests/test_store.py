"""Audit store tests (one engagement). Tenant isolation is tested in test_tenancy.py.
Fixtures (throw-away database, firms and engagements) are in conftest.py."""
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tickmark.extract import LabelExtractor
from tickmark.pipeline import run_vouch
from tickmark.store import add_review, get_run, save_failed_run, save_run, scoped, verify_run
from tickmark.store.schema import AUDIT_TABLES, documents, run_documents, runs, sample_rows
from tickmark.vouch import VouchPolicy

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]
GEN, EXT = ROOT / "data/generated", ROOT / "data/external/tickmark-sample"
PERIOD = {GEN: (date(2025, 1, 1), date(2025, 12, 31)), EXT: (date(2026, 1, 1), date(2026, 12, 31))}


def _run(sample=GEN / "sample.xlsx", invoices=GEN / "invoices", labels=GEN / "labels"):
    if not Path(sample).exists():
        pytest.skip(f"{sample} missing")
    start, end = PERIOD[Path(sample).parent]
    return run_vouch(Path(sample), Path(invoices), LabelExtractor(Path(labels)),
                     VouchPolicy(period_start=start, period_end=end))


def _save(t, run, ctx=None):
    return save_run(run, ctx or t.coke, started_at=datetime.now(UTC), db=t.app)


def test_run_is_stored_completely_and_reads_back_identically(tenancy):
    t = tenancy
    run = _run()
    run_id = _save(t, run)
    stored = get_run(t.coke, run_id, t.app)
    assert stored.results == run.results  # status, files, evidence, candidates, comparisons, steps
    assert stored.verdicts == run.verdicts and len(stored.verdicts) == 16  # agreements, checks, reasons, steps
    assert [e.line_items for e in stored.extracted] == [e.line_items for e in run.extracted]
    assert stored.run["thresholds"]["vouch"]["period_start"] == "2025-01-01"
    assert len(stored.files) == 20 and len(stored.results) == 16 and stored.issues == []
    assert [e.fields for e in stored.extracted] == [e.fields for e in run.extracted]
    assert stored.run["status"] == "completed" and stored.run["run_by"] == "ali"
    assert (stored.run["tenant_id"], stored.run["engagement_id"]) == (t.coke.tenant_id, t.coke.engagement_id)
    assert stored.run["thresholds"]["confidence_min"] == 0.9 and "tickmark" in stored.run["matcher_version"]
    assert verify_run(t.coke, run_id, t.app) == []


def test_issues_are_stored_so_no_row_is_lost(tenancy):
    run = _run(EXT / "sample.xlsx", EXT / "invoices", EXT / "labels")
    stored = get_run(tenancy.coke, _save(tenancy, run), tenancy.app)
    assert len(stored.results) == 7
    assert [(i["excel_row"], i["kind"]) for i in stored.issues] == [(10, "error")]
    assert stored.issues[0]["raw_cells"][0].startswith("Synthetic test data")


@pytest.mark.parametrize("table", AUDIT_TABLES)
def test_audit_tables_are_append_only_for_the_app_and_even_for_the_owner(tenancy, table):
    t = tenancy
    run_id = _save(t, _run())
    add_review(t.coke, run_id, "1", decision="accept", db=t.app)  # so every table has a row to attack
    col = "tenant_id"
    for sql in (f"UPDATE {table} SET {col} = {col}", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
        with pytest.raises(DBAPIError, match="permission denied"), scoped(t.coke, t.app) as c:  # the app: no grant
            c.execute(text(sql))
        with pytest.raises(DBAPIError, match="append-only"), t.admin.begin() as c:  # the owner: the trigger
            c.execute(text(sql))


def test_rerun_adds_a_new_run_and_keeps_the_old_one(tenancy):
    t = tenancy
    run = _run()
    first = _save(t, run)
    before = get_run(t.coke, first, t.app)
    second = _save(t, run)
    assert first != second
    assert get_run(t.coke, first, t.app).results == before.results
    with scoped(t.coke, t.app) as c:  # same bytes in the same engagement -> no duplicate documents
        shas = c.execute(select(documents.c.sha256)).scalars().all()
    assert len(shas) == len(set(shas))


def test_same_bytes_under_two_names_is_one_document(tenancy, tmp_path):
    t = tenancy
    folder, labels = tmp_path / "invoices", tmp_path / "labels"
    folder.mkdir()
    labels.mkdir()
    for name in ("a_copy", "b_copy"):
        shutil.copy(GEN / "invoices" / "invoice_001.pdf", folder / f"{name}.pdf")
        shutil.copy(GEN / "labels" / "invoice_001.json", labels / f"{name}.json")
    run_id = _save(t, _run(GEN / "sample.xlsx", folder, labels))
    with scoped(t.coke, t.app) as c:
        docs = c.execute(select(run_documents.c.document_id, run_documents.c.file_name)
                         .where(run_documents.c.run_id == run_id)).all()
    assert {d.file_name for d in docs} == {"a_copy.pdf", "b_copy.pdf"} and len({d.document_id for d in docs}) == 1
    # row 1 now has two identical candidates -> review, never a guess
    row1 = next(r for r in get_run(t.coke, run_id, t.app).results if r.row_id == "1")
    assert row1.status == "needs_review"


def test_reviews_append_and_latest_is_kept_with_history(tenancy):
    t = tenancy
    run_id = _save(t, _run())
    add_review(t.coke, run_id, "5", decision="requested_from_client", note="asked for invoice", db=t.app)
    add_review(replace(t.coke, user="sara"), run_id, "5", decision="choose_candidate",
               chosen_file="invoice_005.pdf", db=t.app)
    history = get_run(t.coke, run_id, t.app).reviews["5"]
    assert [(r.reviewer, r.decision, r.chosen_file) for r in history] == [
        ("ali", "requested_from_client", None), ("sara", "choose_candidate", "invoice_005.pdf")]
    assert get_run(t.coke, run_id, t.app).results[4].status == "no_evidence"  # the tool's result is untouched


def test_review_rules_are_enforced(tenancy):
    t = tenancy
    run_id = _save(t, _run())
    with pytest.raises(ValueError):
        add_review(t.coke, run_id, "1", decision="approve", db=t.app)
    with pytest.raises(KeyError):
        add_review(t.coke, run_id, "1", decision="choose_candidate", chosen_file="not_in_run.pdf", db=t.app)
    with pytest.raises(IntegrityError, match="reviews_choice_has_document"):  # the database checks it too
        add_review(t.coke, run_id, "1", decision="choose_candidate", db=t.app)


def test_tampered_evidence_is_detected(tenancy):
    t = tenancy
    run_id = _save(t, _run())
    victim = Path(get_run(t.coke, run_id, t.app).files[0]["storage_path"])
    victim.write_bytes(victim.read_bytes() + b"%tampered")
    assert any("stored file changed" in p for p in verify_run(t.coke, run_id, t.app))


def test_failed_run_is_recorded(tenancy):
    t = tenancy
    run_id = save_failed_run(t.coke, sample_path=GEN / "sample.xlsx", started_at=datetime.now(UTC),
                             error="DocIntError: not configured", db=t.app)
    with scoped(t.coke, t.app) as c:
        row = c.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
        n_rows = c.execute(select(func.count()).select_from(sample_rows)
                           .where(sample_rows.c.run_id == run_id)).scalar_one()
    assert (row["status"], row["error"], n_rows) == ("failed", "DocIntError: not configured", 0)
