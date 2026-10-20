"""Multi-tenant guarantees, enforced by Postgres (row-level security, composite foreign keys,
grants), not only by the code. Firm A audits Coke and Pepsi; firm B audits Acme (see conftest.py)."""
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from tests.conftest import alembic_config
from tickmark.extract import LabelExtractor
from tickmark.pipeline import run_identification
from tickmark.store import get_run, list_runs, save_run, scoped
from tickmark.store.schema import AUDIT_TABLES, documents, engagements, run_documents, tenants

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "data/generated"


def _save(t, ctx, invoices=GEN / "invoices", labels=GEN / "labels"):
    if not (GEN / "sample.xlsx").exists():
        pytest.skip("generated data missing")
    run = run_identification(GEN / "sample.xlsx", Path(invoices), LabelExtractor(Path(labels)))
    return save_run(run, ctx, started_at=datetime.now(UTC), db=t.app)


def _counts(conn) -> dict[str, int]:
    return {tb: conn.execute(text(f"SELECT count(*) FROM {tb}")).scalar_one() for tb in AUDIT_TABLES}


def test_tenant_a_cannot_read_tenant_b(tenancy):
    t = tenancy
    run_id = _save(t, t.coke)
    with pytest.raises(KeyError, match="no run"):  # "no run", not "forbidden": its existence is not revealed
        get_run(t.acme, run_id, t.app)
    assert run_id not in {r["id"] for r in list_runs(t.acme, t.app)}
    with scoped(t.acme, t.app) as c:
        assert all(n == 0 for n in _counts(c).values()), "firm B sees none of firm A's rows"
        assert c.execute(select(tenants.c.name)).scalars().all() == ["Firm B"]
        assert c.execute(select(engagements.c.client_name)).scalars().all() == ["Acme"]


def test_ethical_wall_between_engagements_of_one_firm(tenancy):
    t = tenancy
    coke_run = _save(t, t.coke)
    with pytest.raises(KeyError):
        get_run(t.pepsi, coke_run, t.app)
    with scoped(t.pepsi, t.app) as c:
        assert c.execute(select(func.count()).select_from(run_documents)
                         .where(run_documents.c.run_id == coke_run)).scalar_one() == 0
        assert c.execute(select(engagements.c.client_name)).scalars().all() == ["Pepsi"]


def test_without_context_nothing_is_visible_or_writable(tenancy):
    t = tenancy
    _save(t, t.coke)
    with t.app.begin() as c:  # no scoped(): no tenant, no engagement
        assert all(n == 0 for n in _counts(c).values())
        assert c.execute(select(func.count()).select_from(tenants)).scalar_one() == 0
    with pytest.raises(ProgrammingError, match="row-level security"), t.app.begin() as c:
        c.execute(documents.insert().values(tenant_id=t.coke.tenant_id, engagement_id=t.coke.engagement_id,
                                            sha256="0" * 64, kind="pdf", size_bytes=1, storage_path="x"))


def test_rows_cannot_be_written_into_another_tenant(tenancy):
    t = tenancy
    with pytest.raises(ProgrammingError, match="row-level security"), scoped(t.coke, t.app) as c:
        c.execute(documents.insert().values(tenant_id=t.acme.tenant_id, engagement_id=t.acme.engagement_id,
                                            sha256="1" * 64, kind="pdf", size_bytes=1, storage_path="x"))


def test_links_across_engagements_are_rejected_by_the_database(tenancy):
    """Even with forged ids, a Coke review cannot point at a Pepsi document."""
    t = tenancy
    coke_run, pepsi_run = _save(t, t.coke), _save(t, t.pepsi)
    pepsi_doc = get_run(t.pepsi, pepsi_run, t.app).files[0]["document_id"]
    with scoped(t.coke, t.app) as c:
        coke_result = c.execute(text("SELECT id FROM match_results WHERE run_id = :r LIMIT 1"),
                                {"r": coke_run}).scalar_one()
    with pytest.raises(IntegrityError, match="reviews_chosen_document_id_fk"), scoped(t.coke, t.app) as c:
        c.execute(text("INSERT INTO reviews (tenant_id, engagement_id, match_result_id, reviewer, decision, "
                       "chosen_document_id) VALUES (:t, :e, :m, 'ali', 'choose_candidate', :d)"),
                  {"t": t.coke.tenant_id, "e": t.coke.engagement_id, "m": coke_result, "d": pepsi_doc})


def test_same_pdf_in_two_engagements_is_stored_twice(tenancy):
    t = tenancy
    coke = get_run(t.coke, _save(t, t.coke), t.app).files[0]
    pepsi = get_run(t.pepsi, _save(t, t.pepsi), t.app).files[0]
    assert coke["sha256"] == pepsi["sha256"]  # same bytes ...
    assert coke["document_id"] != pepsi["document_id"]  # ... separate documents
    assert coke["storage_path"] != pepsi["storage_path"]  # ... separate evidence copies
    assert f"/{t.coke.tenant_id}/{t.coke.engagement_id}/" in coke["storage_path"]
    assert f"/{t.pepsi.tenant_id}/{t.pepsi.engagement_id}/" in pepsi["storage_path"]


@pytest.mark.parametrize("sql", [
    "ALTER TABLE runs DISABLE ROW LEVEL SECURITY",
    "ALTER TABLE runs NO FORCE ROW LEVEL SECURITY",
    "DROP POLICY runs_in_scope ON runs",
    "DROP TRIGGER runs_append_only ON runs",
    "INSERT INTO tenants (name) VALUES ('Evil Corp')",
    "INSERT INTO engagements (tenant_id, client_name) SELECT id, 'x' FROM tenants",
    "CREATE TABLE sneaky (x int)",
])
def test_app_role_cannot_weaken_the_rules(tenancy, sql):
    with pytest.raises(DBAPIError, match="must be owner|permission denied"), scoped(tenancy.coke, tenancy.app) as c:
        c.execute(text(sql))


def test_multi_tenant_migration_refuses_to_run_over_existing_rows(make_database):
    url = make_database("0001")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO documents (sha256, kind, size_bytes, storage_path) "
                       "VALUES (repeat('a', 64), 'pdf', 1, 'x')"))
    engine.dispose()
    with pytest.raises(DBAPIError, match="audit tables hold data"):
        command.upgrade(alembic_config(url), "head")


def test_evidence_folders_are_separate(tenancy, tmp_path):
    """The same file name in two engagements lands in two folders, never overwriting each other."""
    t = tenancy
    folder, labels = tmp_path / "inv", tmp_path / "lab"
    folder.mkdir()
    labels.mkdir()
    shutil.copy(GEN / "invoices" / "invoice_001.pdf", folder / "invoice_001.pdf")
    shutil.copy(GEN / "labels" / "invoice_001.json", labels / "invoice_001.json")
    paths = {get_run(ctx, _save(t, ctx, folder, labels), t.app).files[0]["storage_path"] for ctx in (t.coke, t.acme)}
    assert len(paths) == 2 and all(Path(p).is_file() for p in paths)
