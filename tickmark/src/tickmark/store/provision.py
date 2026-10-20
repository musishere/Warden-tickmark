"""Register tenants (audit firms) and engagements (clients). Owner role only; the app cannot write these.

Warden is the source of truth for both. These rows exist so every audit row can point at a real
tenant + engagement; pass Warden's ids when you have them.
"""
from uuid import UUID

from sqlalchemy import Engine, create_engine, select

from tickmark.config import settings
from tickmark.store.schema import engagements, tenants


def admin_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.admin_database_url)


def add_tenant(name: str, entra_tenant_id: str | None = None, id: UUID | None = None,
               db: Engine | None = None) -> UUID:
    values = {"name": name, "entra_tenant_id": entra_tenant_id} | ({"id": id} if id else {})
    with (db or admin_engine()).begin() as conn:
        return conn.execute(tenants.insert().values(**values).returning(tenants.c.id)).scalar_one()


def add_engagement(tenant_id: UUID, client_name: str, id: UUID | None = None, db: Engine | None = None) -> UUID:
    values = {"tenant_id": tenant_id, "client_name": client_name} | ({"id": id} if id else {})
    with (db or admin_engine()).begin() as conn:
        return conn.execute(engagements.insert().values(**values).returning(engagements.c.id)).scalar_one()


def list_tenants(db: Engine | None = None) -> list[dict]:
    with (db or admin_engine()).connect() as conn:
        rows = conn.execute(select(tenants.c.id, tenants.c.name, tenants.c.entra_tenant_id,
                                   engagements.c.id.label("engagement_id"), engagements.c.client_name)
                            .outerjoin(engagements, engagements.c.tenant_id == tenants.c.id)
                            .order_by(tenants.c.name, engagements.c.client_name)).mappings()
        return [dict(r) for r in rows]
