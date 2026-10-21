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
        if number:  # a number used once is no duplicate, whoever the vendor is
            keyed.append((inv.file, normalize_vendor(vendor) if vendor else "", normalize_number(number), number))
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


def pct(rate: Decimal) -> str:
    """0.19 -> '19%', 0.055 -> '5.5%'."""
    return f"{(rate * 100).normalize():f}%"


def vat_rate(inv: ExtractedInvoice, policy: VouchPolicy) -> CheckResult:
    """With a VAT breakdown per rate: every rate allowed and the parts add up to the tax. Without one:
    the tax is one allowed rate of the net; a share between two allowed rates may be several rates,
    so it is not checkable (👀), not a failure."""
    snips = _snips(inv, "subtotal", "tax")
    allowed = ", ".join(pct(r) for r in policy.vat_rates)
    if inv.tax_details:
        (tax,), missing = _amounts(inv, "tax")
        parts = [(Decimal(d["rate"]) / 100, Decimal(d["amount"])) for d in inv.tax_details]
        if bad := sorted({r for r, _ in parts if all(abs(r - a) > Decimal("0.0001") for a in policy.vat_rates)}):
            return CheckResult(name="vat_rate", status="fail", snips=snips,
                               detail=f"rate {', '.join(pct(r) for r in bad)} not allowed ({allowed})")
        shown = " + ".join(f"{a} at {pct(r)}" for r, a in parts)
        if missing:
            return CheckResult(name="vat_rate", status="not_checkable", detail=missing, snips=snips)
        if abs(sum(a for _, a in parts) - tax) <= policy.amount_tol:
            return CheckResult(name="vat_rate", status="pass", detail=f"tax {tax} = {shown}", snips=snips)
        return CheckResult(name="vat_rate", status="not_checkable", snips=snips,
                           detail=f"the VAT breakdown {shown} does not add up to the tax {tax}")
    (sub, tax), missing = _amounts(inv, "subtotal", "tax")
    if missing:
        return CheckResult(name="vat_rate", status="not_checkable", detail=missing, snips=snips)
    rate = vat_rate_matches(sub, tax, policy.vat_rates, policy.amount_tol)
    if rate is not None:
        return CheckResult(name="vat_rate", status="pass", detail=f"tax {tax} = {rate:%} of {sub}", snips=snips)
    share = tax / sub if sub else None
    one = (share * 200).quantize(Decimal(1)) / 200 if share is not None else None  # nearest 0.5 %
    if one is not None and abs(sub * one - tax) <= policy.amount_tol:  # one rate, just not an allowed one
        return CheckResult(name="vat_rate", status="fail", snips=snips,
                           detail=f"tax {tax} is {pct(one)} of {sub}: not an allowed rate ({allowed})")
    if share is not None and min(policy.vat_rates) < share < max(policy.vat_rates):
        return CheckResult(name="vat_rate", status="not_checkable", snips=snips,
                           detail=f"tax {tax} is {share:.2%} of {sub}: between allowed rates ({allowed}), "
                                  "perhaps several rates; no breakdown per rate was read")
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
    status = next((st for s, st in (("fail", "fail"), ("unclear", "not_checkable")) if any(x == s for x, _ in found)),
                  "warn")
    return CheckResult(name="duplicate", status=status, detail="; ".join(d for _, d in found), snips=snips)


def check_invoice(inv: ExtractedInvoice, policy: VouchPolicy,
                  dups: dict[str, list[tuple[str, str]]]) -> list[CheckResult]:
    lines = [lines_sum(inv, policy.amount_tol)] if policy.line_items else []  # the engagement's choice
    return [totals_reconcile(inv, policy.amount_tol), *lines, vat_rate(inv, policy), in_period(inv, policy),
            duplicate(inv, dups)]
