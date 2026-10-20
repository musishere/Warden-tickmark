"""Saved column mappings, per engagement: which column of a client's sheet holds which field.

Append-only like every audit table: a corrected mapping is a new row, and the latest confirmed one
for the same definition and header layout is current. A run records the mapping it used.
"""
from uuid import UUID

from sqlalchemy import Connection, Engine, select

from tickmark.fields.mapping import ColumnMapping
from tickmark.models import Cell
from tickmark.store.context import TenantContext, scoped
from tickmark.store.schema import column_mappings

_SOURCE_IN_DB = {"aliases": "aliases", "claude": "claude", "person": "person", "saved": "person"}


def insert_mapping(conn: Connection, ctx: TenantContext, mapping: ColumnMapping, headers: list[Cell]) -> UUID:
    """Store a mapping (inside the caller's scoped transaction); an identical one is reused."""
    source = _SOURCE_IN_DB[mapping.source]
    same = conn.execute(select(column_mappings.c.id).where(
        column_mappings.c.definition == mapping.definition,
        column_mappings.c.definition_version == mapping.version,
        column_mappings.c.headers_sha256 == mapping.headers_sha256,
        column_mappings.c.columns == mapping.columns,
        column_mappings.c.source == source,
        column_mappings.c.confirmed_by.is_not_distinct_from(mapping.confirmed_by),
    ).order_by(column_mappings.c.created_at.desc()).limit(1)).scalar_one_or_none()
    if same:
        return same
    return conn.execute(column_mappings.insert().values(
        tenant_id=ctx.tenant_id, engagement_id=ctx.engagement_id, definition=mapping.definition,
        definition_version=mapping.version, headers=[{"col": h.col, "header": h.header} for h in headers],
        headers_sha256=mapping.headers_sha256, columns=mapping.columns, source=source, notes=mapping.notes,
        proposal=mapping.proposal, confirmed_by=mapping.confirmed_by,
    ).returning(column_mappings.c.id)).scalar_one()


def save_mapping(ctx: TenantContext, mapping: ColumnMapping, headers: list[Cell], db: Engine | None = None) -> UUID:
    with scoped(ctx, db) as conn:
        return insert_mapping(conn, ctx, mapping, headers)


def _to_mapping(row) -> ColumnMapping:
    return ColumnMapping(definition=row.definition, version=row.definition_version, columns=row.columns,
                         source=row.source, confirmed_by=row.confirmed_by, headers_sha256=row.headers_sha256,
                         notes=row.notes, proposal=row.proposal)


def current_mapping(ctx: TenantContext, definition: str, version: int, headers_sha256: str,
                    db: Engine | None = None) -> ColumnMapping | None:
    """The latest confirmed mapping this engagement saved for this definition and header layout."""
    with scoped(ctx, db) as conn:
        row = conn.execute(select(column_mappings).where(
            column_mappings.c.definition == definition, column_mappings.c.definition_version == version,
            column_mappings.c.headers_sha256 == headers_sha256, column_mappings.c.confirmed_by.is_not(None),
        ).order_by(column_mappings.c.created_at.desc(), column_mappings.c.id).limit(1)).one_or_none()
    if row is None:
        return None
    m = _to_mapping(row)
    return m.model_copy(update={"source": "saved", "notes": m.notes | {"_saved_as": str(row.id)}})


def mapping_by_id(conn: Connection, mapping_id: UUID) -> ColumnMapping | None:
    row = conn.execute(select(column_mappings).where(column_mappings.c.id == mapping_id)).one_or_none()
    return _to_mapping(row) if row else None
