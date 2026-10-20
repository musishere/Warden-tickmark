"""Audit trail in Postgres: every run, the exact files it used (by hash), its results and human reviews.

Append-only. The rules that decide stay in code (tickmark.match); this only records what happened.
"""
from tickmark.store.queries import DECISIONS, StoredRun, add_review, get_run, list_runs
from tickmark.store.record import save_failed_run, save_run
from tickmark.store.verify import verify_run

__all__ = ["DECISIONS", "StoredRun", "add_review", "get_run", "list_runs", "save_failed_run", "save_run",
           "verify_run"]
