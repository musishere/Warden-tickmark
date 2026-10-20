"""Register tenants (audit firms) and engagements (clients). Owner role only; the app cannot write these.

Warden is the source of truth for both. These rows exist so every audit row can point at a real
tenant + engagement; pass Warden's ids when you have them.
"""
from datetime import date
from uuid import UUID

from sqlalchemy import Engine, create_engine, select, update

from tickmark.config import settings
from tickmark.store.schema import engagements, tenants


def admin_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.admin_database_url)


def add_tenant(name: str, entra_tenant_id: str | None = None, id: UUID | None = None,
               db: Engine | None = None) -> UUID:
    values = {"name": name, "entra_tenant_id": entra_tenant_id} | ({"id": id} if id else {})
    with (db or admin_engine()).begin() as conn:
        return conn.execute(tenants.insert().values(**values).returning(tenants.c.id)).scalar_one()


def add_engagement(tenant_id: UUID, client_name: str, id: UUID | None = None,
                   period: tuple[date, date] | None = None, db: Engine | None = None) -> UUID:
    values = {"tenant_id": tenant_id, "client_name": client_name} | ({"id": id} if id else {})
    if period:
        values |= {"period_start": period[0], "period_end": period[1]}
    with (db or admin_engine()).begin() as conn:
        return conn.execute(engagements.insert().values(**values).returning(engagements.c.id)).scalar_one()


def set_period(engagement_id: UUID, period: tuple[date, date], db: Engine | None = None) -> None:
    """The audit period (Warden's). engagements is a registry, not an audit table, so it can change."""
    with (db or admin_engine()).begin() as conn:
        n = conn.execute(update(engagements).where(engagements.c.id == engagement_id)
                         .values(period_start=period[0], period_end=period[1])).rowcount
    if n != 1:
        raise KeyError(f"no engagement {engagement_id}")


def list_tenants(db: Engine | None = None) -> list[dict]:
    with (db or admin_engine()).connect() as conn:
        rows = conn.execute(select(tenants.c.id, tenants.c.name, tenants.c.entra_tenant_id,
                                   engagements.c.id.label("engagement_id"), engagements.c.client_name,
                                   engagements.c.period_start, engagements.c.period_end)
                            .outerjoin(engagements, engagements.c.tenant_id == tenants.c.id)
                            .order_by(tenants.c.name, engagements.c.client_name)).mappings()
        return [dict(r) for r in rows]
