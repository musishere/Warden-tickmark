"""Who is acting, for which audit firm and which client engagement.

Every store call runs inside scoped(): a transaction that first tells Postgres the tenant and
engagement (transaction-local settings, so a pooled connection never carries them over). Row-level
security then shows and accepts only that engagement's rows. Without a context nothing is visible.

The ids are Warden's. In the API they must come from the verified token, never from user input.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from tickmark.store.db import engine as default_engine


@dataclass(frozen=True)
class TenantContext:
    tenant_id: UUID
    engagement_id: UUID
    user: str

    def __post_init__(self) -> None:  # accept strings from the CLI, store UUIDs
        object.__setattr__(self, "tenant_id", UUID(str(self.tenant_id)))
        object.__setattr__(self, "engagement_id", UUID(str(self.engagement_id)))
        if not self.user:
            raise ValueError("TenantContext needs the acting user")


@contextmanager
def scoped(ctx: TenantContext, db: Engine | None = None) -> Iterator[Connection]:
    with (db or default_engine()).begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.engagement_id', :e, true)"),
                     {"t": str(ctx.tenant_id), "e": str(ctx.engagement_id)})
        yield conn
