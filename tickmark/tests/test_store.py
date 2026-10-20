"""Audit store tests. They need the Tickmark Postgres (docker compose up -d) and run in a fresh
throw-away database, migrated with Alembic, which is dropped afterwards."""
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from tickmark.config import settings
from tickmark.extract import LabelExtractor
from tickmark.pipeline import run_identification
from tickmark.store import add_review, get_run, save_failed_run, save_run, verify_run
from tickmark.store.schema import AUDIT_TABLES, documents, run_documents, runs, sample_rows

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]
GEN, EXT = ROOT / "data/generated", ROOT / "data/external/tickmark-sample"


@pytest.fixture(scope="module")
def db():
    admin_url = make_url(settings.database_url)
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("select 1"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tickmark Postgres not reachable ({e.__class__.__name__}); run: docker compose up -d")
    name = f"tickmark_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name)
    cfg = Config(str(ROOT / "alembic.ini"), attributes={"configure_logger": False})
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(autouse=True)
def evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "store"))


def _run(sample=GEN / "sample.xlsx", invoices=GEN / "invoices", labels=GEN / "labels"):
    if not Path(sample).exists():
        pytest.skip(f"{sample} missing")
    return run_identification(Path(sample), Path(invoices), LabelExtractor(Path(labels)))


def _save(db, run, **kw):
    return save_run(run, run_by="test", started_at=datetime.now(UTC), db=db, **kw)


def test_run_is_stored_completely_and_reads_back_identically(db):
    run = _run()
    run_id = _save(db, run, engagement_id="ENG-1")
    stored = get_run(run_id, db)
    assert stored.results == run.results  # status, files, evidence, candidates, comparisons, steps
    assert len(stored.files) == 20 and len(stored.results) == 16 and stored.issues == []
    assert [e.fields for e in stored.extracted] == [e.fields for e in run.extracted]
    assert stored.run["status"] == "completed" and stored.run["engagement_id"] == "ENG-1"
    assert stored.run["thresholds"]["confidence_min"] == 0.9 and "tickmark" in stored.run["matcher_version"]
    assert verify_run(run_id, db) == []


def test_issues_are_stored_so_no_row_is_lost(db):
    run = _run(EXT / "sample.xlsx", EXT / "invoices", EXT / "labels")
    stored = get_run(_save(db, run), db)
    assert len(stored.results) == 7
    assert [(i["excel_row"], i["kind"]) for i in stored.issues] == [(10, "error")]
    assert stored.issues[0]["raw_cells"][0].startswith("Synthetic test data")


@pytest.mark.parametrize("table", AUDIT_TABLES)
def test_audit_tables_reject_update_and_delete(db, table):
    run_id = _save(db, _run())
    add_review(run_id, "1", reviewer="ali", decision="accept", db=db)  # so every table has a row to attack
    for sql in (f"UPDATE {table} SET {_any_column(db, table)} = {_any_column(db, table)}", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="append-only"), db.begin() as c:
            c.execute(text(sql))


def _any_column(db, table):
    with db.connect() as c:
        return c.execute(text("select column_name from information_schema.columns where table_name = :t "
                              "order by ordinal_position limit 1"), {"t": table}).scalar_one()


def test_rerun_adds_a_new_run_and_keeps_the_old_one(db):
    run = _run()
    first = _save(db, run)
    before = get_run(first, db)
    second = _save(db, run)
    assert first != second
    assert get_run(first, db).results == before.results
    with db.connect() as c:  # same bytes -> no duplicate documents
        shas = c.execute(select(documents.c.sha256)).scalars().all()
    assert len(shas) == len(set(shas))


def test_same_bytes_under_two_names_is_one_document(db, tmp_path):
    folder = tmp_path / "invoices"
    folder.mkdir()
    labels = tmp_path / "labels"
    labels.mkdir()
    for name in ("a_copy", "b_copy"):
        shutil.copy(GEN / "invoices" / "invoice_001.pdf", folder / f"{name}.pdf")
        shutil.copy(GEN / "labels" / "invoice_001.json", labels / f"{name}.json")
    run_id = _save(db, _run(GEN / "sample.xlsx", folder, labels))
    with db.connect() as c:
        docs = c.execute(select(run_documents.c.document_id, run_documents.c.file_name)
                         .where(run_documents.c.run_id == run_id)).all()
    assert {d.file_name for d in docs} == {"a_copy.pdf", "b_copy.pdf"} and len({d.document_id for d in docs}) == 1
    # row 1 now has two identical candidates -> review, never a guess
    row1 = next(r for r in get_run(run_id, db).results if r.row_id == "1")
    assert row1.status == "needs_review"


def test_reviews_append_and_latest_is_kept_with_history(db):
    run_id = _save(db, _run(GEN / "sample.xlsx", GEN / "invoices", GEN / "labels"))
    add_review(run_id, "5", reviewer="ali", decision="requested_from_client", note="asked for invoice", db=db)
    add_review(run_id, "5", reviewer="sara", decision="choose_candidate", chosen_file="invoice_005.pdf", db=db)
    history = get_run(run_id, db).reviews["5"]
    assert [(r.reviewer, r.decision, r.chosen_file) for r in history] == [
        ("ali", "requested_from_client", None), ("sara", "choose_candidate", "invoice_005.pdf")]
    assert get_run(run_id, db).results[4].status == "no_evidence"  # the tool's result is untouched


def test_review_rules_are_enforced(db):
    run_id = _save(db, _run())
    with pytest.raises(ValueError):
        add_review(run_id, "1", reviewer="ali", decision="approve", db=db)
    with pytest.raises(KeyError):
        add_review(run_id, "1", reviewer="ali", decision="choose_candidate", chosen_file="not_in_run.pdf", db=db)
    with pytest.raises(IntegrityError, match="reviews_choice_has_document"):  # the database checks it too
        add_review(run_id, "1", reviewer="ali", decision="choose_candidate", db=db)


def test_tampered_evidence_is_detected(db):
    run_id = _save(db, _run())
    stored = get_run(run_id, db)
    victim = Path(stored.files[0]["storage_path"])
    victim.chmod(0o644)
    victim.write_bytes(victim.read_bytes() + b"%tampered")
    assert any("stored file changed" in p for p in verify_run(run_id, db))


def test_failed_run_is_recorded(db):
    run_id = save_failed_run(sample_path=GEN / "sample.xlsx", run_by="test", started_at=datetime.now(UTC),
                             error="DocIntError: not configured", db=db)
    with db.connect() as c:
        row = c.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
        n_rows = c.execute(select(func.count()).select_from(sample_rows)
                           .where(sample_rows.c.run_id == run_id)).scalar_one()
    assert (row["status"], row["error"], n_rows) == ("failed", "DocIntError: not configured", 0)
