"""Verify each row's claim against its linked PDF: ✅ matched · ⚠️ difference · 👀 needs review · ❓ no evidence."""
from tickmark.vouch.invoice_checks import CHECKS, check_invoice, duplicate_map
from tickmark.vouch.policy import VouchPolicy, parse_period, parse_rates
from tickmark.vouch.verdict import MARK, vouch

__all__ = ["CHECKS", "MARK", "VouchPolicy", "check_invoice", "duplicate_map", "parse_period", "parse_rates", "vouch"]
