"""Audit trail in Postgres: every run, the exact files it used (by hash), its results and human reviews.

Append-only and multi-tenant: every call takes a TenantContext (audit firm + engagement), and
Postgres row-level security keeps each engagement's data to itself. The rules that decide stay in
code (tickmark.match); this only records what happened.
"""
from tickmark.store.context import TenantContext, scoped
from tickmark.store.queries import (
           DECISIONS,
           StoredRun,
           add_review,
           engagement_currency,
           engagement_period,
           get_run,
           list_runs,
)
from tickmark.store.record import save_failed_run, save_run
from tickmark.store.verify import verify_run

__all__ = [
           "DECISIONS",
           "StoredRun",
           "TenantContext",
           "add_review", "engagement_currency", "engagement_period",
           "get_run",
           "list_runs",
           "save_failed_run",
           "save_run",
           "scoped",
           "verify_run",
]
