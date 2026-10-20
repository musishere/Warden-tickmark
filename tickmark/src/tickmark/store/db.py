from functools import cache

from sqlalchemy import Engine, create_engine

from tickmark.config import settings


@cache
def engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.database_url, pool_pre_ping=True)
