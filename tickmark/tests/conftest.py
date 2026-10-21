"""Shared fixtures for the audit-store tests (marked db). They need the Tickmark Postgres
(docker compose up -d) and run in a fresh throw-away database, dropped afterwards.

Two audit firms: firm A audits Coke and Pepsi (an ethical wall between them), firm B audits Acme.
The app connects as tickmark_app (row-level security applies); provisioning uses the owner.
"""
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("TICKMARK_LOG_DIR", tempfile.mkdtemp(prefix="tickmark-test-logs-"))  # not data/logs
os.environ.setdefault("TICKMARK_WEB_DEFAULT_ENGAGEMENT", "off")  # tests never write to the dev database
os.environ.setdefault("TICKMARK_LLM_MAPPING", "off")  # nor call the LLM (test_llm_live.py does, opt-in)

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

from tickmark.config import settings
from tickmark.store import TenantContext
from tickmark.store.provision import add_engagement, add_tenant

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Tenancy:
    url: URL
    admin: Engine  # owner of the test database (superuser locally): provisioning + attacks on triggers
    app: Engine  # tickmark_app: what the code uses
    coke: TenantContext  # firm A
    pepsi: TenantContext  # firm A, behind an ethical wall from Coke
    acme: TenantContext  # firm B


def alembic_config(url: URL) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"), attributes={"configure_logger": False})
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def server():
    """An AUTOCOMMIT owner connection to the Postgres server, for creating test databases."""
    url = make_url(settings.admin_database_url)
    try:
        admin = create_engine(url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("select 1"))
            c.execute(text((ROOT / "docker/initdb/01-app-role.sql").read_text()))  # ensure the app login role
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tickmark Postgres not reachable ({e.__class__.__name__}); run: docker compose up -d")
    yield admin, url
    admin.dispose()


@pytest.fixture(scope="session")
def make_database(server):
    admin, url = server
    created = []

    def make(revision: str = "head") -> URL:
        name = f"tickmark_test_{uuid.uuid4().hex[:10]}"
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        db_url = url.set(database=name)
        command.upgrade(alembic_config(db_url), revision)
        return db_url

    yield make
    with admin.connect() as c:
        for name in created:
            c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def tenancy(make_database) -> Tenancy:
    url = make_database()
    admin = create_engine(url)
    a = add_tenant("Firm A", entra_tenant_id="entra-a", db=admin)
    b = add_tenant("Firm B", entra_tenant_id="entra-b", db=admin)
    coke, pepsi = add_engagement(a, "Coke", db=admin), add_engagement(a, "Pepsi", db=admin)
    acme = add_engagement(b, "Acme", db=admin)
    app_url = make_url(settings.database_url).set(database=url.database)
    app = create_engine(app_url)
    yield Tenancy(url, admin, app, TenantContext(a, coke, "ali"), TenantContext(a, pepsi, "sara"),
                  TenantContext(b, acme, "bob"))
    app.dispose()
    admin.dispose()


@pytest.fixture(autouse=True)
def evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "store"))
