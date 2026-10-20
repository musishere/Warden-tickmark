"""The invoice on its own: do its numbers add up, is the VAT right, is it in the audit period, is it a
duplicate? Each check is pass / fail / warn, or not_checkable when the values it needs were not read
reliably (which keeps a verdict from being ✅)."""
from decimal import Decimal, InvalidOperation

from tickmark.checks.deterministic import (
    date_in_period,
    duplicates,
    line_arithmetic,
    line_items_sum_to_total,
    money,
    vat_rate_matches,
)
from tickmark.match.identify import CONFIDENCE_MIN, normalize_number
from tickmark.models import CheckResult, ExtractedInvoice, Snip
from tickmark.sample import normalize_vendor
from tickmark.vouch.compare import read, read_amount, read_date
from tickmark.vouch.policy import VouchPolicy

CHECKS = ("totals_reconcile", "lines_sum", "vat_rate", "in_period", "duplicate")


def _snips(inv: ExtractedInvoice, *names: str) -> list[Snip]:
    return [f.snip for n in names if (f := inv.get(n)) and f.snip]


def duplicate_map(invoices: list[ExtractedInvoice]) -> dict[str, list[tuple[str, str]]]:
    keyed = []
    for inv in invoices:
        number, _ = read(inv, "invoice_number")
        vendor, _ = read(inv, "vendor")
        if number and vendor:
            keyed.append((inv.file, normalize_vendor(vendor), normalize_number(number), number))
    return duplicates(keyed)


def _amounts(inv: ExtractedInvoice, *names: str) -> tuple[list[Decimal], str]:
    values, missing = [], []
    for n in names:
        v, why = read_amount(inv, n)
        if v is None:
            missing.append(f"{n} {why}")
        values.append(v)
    return values, "; ".join(missing)


def totals_reconcile(inv: ExtractedInvoice, tol: Decimal) -> CheckResult:
    (sub, tax, total), missing = _amounts(inv, "subtotal", "tax", "total")
    snips = _snips(inv, "subtotal", "tax", "total")
    if missing:
        return CheckResult(name="totals_reconcile", status="not_checkable", detail=missing, snips=snips)
    ok = abs(sub + tax - total) <= tol
    return CheckResult(name="totals_reconcile", status="pass" if ok else "fail", snips=snips,
                       detail=f"{sub} + {tax} = {sub + tax} {'=' if ok else '≠'} total {total}")


def lines_sum(inv: ExtractedInvoice, tol: Decimal) -> CheckResult:
    (sub,), missing = _amounts(inv, "subtotal")
    snips = _snips(inv, "subtotal") + [li.snip for li in inv.line_items if li.snip]
    if missing:
        return CheckResult(name="lines_sum", status="not_checkable", detail=missing, snips=snips)
    if not inv.line_items:
        return CheckResult(name="lines_sum", status="not_checkable", detail="no line items read", snips=snips)
    amounts, bad = [], []
    for i, li in enumerate(inv.line_items, 1):
        if li.amount is None or li.confidence is None or li.confidence < CONFIDENCE_MIN:
            return CheckResult(name="lines_sum", status="not_checkable", snips=snips,
                               detail=f"line {i} not read reliably (confidence {li.confidence})")
        try:
            amount = Decimal(li.amount)
            if li.quantity and li.unit_price and not line_arithmetic(Decimal(li.quantity), Decimal(li.unit_price),
                                                                      amount, tol):
                bad.append(f"line {i}: {li.quantity} × {li.unit_price} = "
                           f"{money(Decimal(li.quantity) * Decimal(li.unit_price))} ≠ {amount}")
        except InvalidOperation:
            return CheckResult(name="lines_sum", status="not_checkable", detail=f"line {i} is not a number",
                               snips=snips)
        amounts.append(amount)
    total = sum(amounts, Decimal(0))
    if not line_items_sum_to_total(amounts, sub, tol):
        bad.insert(0, f"{len(amounts)} lines sum to {total} ≠ subtotal {sub} ({sub - total:+} unexplained)")
    if bad:
        return CheckResult(name="lines_sum", status="fail", detail="; ".join(bad), snips=snips)
    return CheckResult(name="lines_sum", status="pass", detail=f"{len(amounts)} lines sum to subtotal {sub}",
                       snips=snips)


def vat_rate(inv: ExtractedInvoice, policy: VouchPolicy) -> CheckResult:
    (sub, tax), missing = _amounts(inv, "subtotal", "tax")
    snips = _snips(inv, "subtotal", "tax")
    if missing:
        return CheckResult(name="vat_rate", status="not_checkable", detail=missing, snips=snips)
    rate = vat_rate_matches(sub, tax, policy.vat_rates, policy.amount_tol)
    if rate is not None:
        return CheckResult(name="vat_rate", status="pass", detail=f"tax {tax} = {rate:%} of {sub}", snips=snips)
    expected = ", ".join(f"{r:.0%} → {money(sub * r)}" for r in policy.vat_rates)
    return CheckResult(name="vat_rate", status="fail", snips=snips,
                       detail=f"tax {tax} is not an allowed rate of {sub} ({expected})")


def in_period(inv: ExtractedInvoice, policy: VouchPolicy) -> CheckResult:
    d, why = read_date(inv, "invoice_date")
    snips = _snips(inv, "invoice_date")
    if d is None:
        return CheckResult(name="in_period", status="not_checkable", detail=f"invoice date {why}", snips=snips)
    if policy.period_start is None or policy.period_end is None:
        return CheckResult(name="in_period", status="not_checkable", detail="no audit period set", snips=snips)
    ok = date_in_period(d, policy.period_start, policy.period_end)
    return CheckResult(name="in_period", status="pass" if ok else "fail", snips=snips,
                       detail=f"{d} {'inside' if ok else 'outside'} {policy.period_start} … {policy.period_end}")


def duplicate(inv: ExtractedInvoice, dups: dict[str, list[tuple[str, str]]]) -> CheckResult:
    snips = _snips(inv, "invoice_number")
    number, why = read(inv, "invoice_number")
    if number is None:
        return CheckResult(name="duplicate", status="not_checkable", detail=f"invoice number {why}", snips=snips)
    found = dups.get(inv.file, [])
    if not found:
        return CheckResult(name="duplicate", status="pass", detail=f"number {number} used once", snips=snips)
    status = "fail" if any(s == "fail" for s, _ in found) else "warn"
    return CheckResult(name="duplicate", status=status, detail="; ".join(d for _, d in found), snips=snips)


def check_invoice(inv: ExtractedInvoice, policy: VouchPolicy,
                  dups: dict[str, list[tuple[str, str]]]) -> list[CheckResult]:
    return [totals_reconcile(inv, policy.amount_tol), lines_sum(inv, policy.amount_tol), vat_rate(inv, policy),
            in_period(inv, policy), duplicate(inv, dups)]
