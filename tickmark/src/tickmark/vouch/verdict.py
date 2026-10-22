"""Does the linked PDF back the row's claim? One verdict per row, decided in code.

  identification no_evidence  -> ❓ no_evidence
  identification needs_review -> 👀 needs_review (not compared: which PDF it is is not settled)
  linked:
    any field differs or any check fails   -> ⚠️ difference (every field and check listed)
    else a claimed field unverifiable, a possible duplicate not settled, a total nothing shows to
         add up (totals not checkable), or item lines the engagement asked for but that could not
         be read                            -> 👀 needs_review (never ✅ on missing evidence)
    else                                    -> ✅ matched
A check that warns (e.g. the number is also used by another vendor) is shown but does not block ✅, nor
does a side check that could not run (VAT rate, period, item lines when not asked for): the claim
itself is backed. Totals are the exception: a total that matches the row proves nothing if the
invoice's own sums are unknown (measured: an invoice whose lines add up to 2112 under a printed total
of 9312 was passed). Item lines too, once the engagement turns them on (measured: unread lines under
a total they do not add up to were passed).
"""
from tickmark.definitions import INVOICE_VOUCHING, Definition
from tickmark.log import get
from tickmark.models import CleanRow, ExtractedInvoice, Identification, Verdict
from tickmark.vouch.compare import compare
from tickmark.vouch.invoice_checks import check_invoice, duplicate_map
from tickmark.vouch.policy import VouchPolicy

log = get("vouch")
MARK = {"matched": "✅", "difference": "⚠️", "needs_review": "👀", "no_evidence": "❓"}


def vouch(identifications: list[Identification], rows: list[CleanRow], invoices: list[ExtractedInvoice],
          policy: VouchPolicy, definition: Definition = INVOICE_VOUCHING) -> list[Verdict]:
    by_row = {cr.row.excel_row: cr for cr in rows}
    by_file = {inv.file: inv for inv in invoices}
    dups = duplicate_map(invoices)
    verdicts = [_verdict(ident, by_row[ident.excel_row], by_file, policy, dups, definition) for ident in identifications]
    for v in verdicts:
        log.info("row %s %s %s%s", v.row_id, MARK[v.verdict], v.verdict, f": {'; '.join(v.reasons)}" if v.reasons else "")
        for a in v.agreements:
            log.debug("row %s   %s: row %s, PDF %s → %s %s", v.row_id, a.field, a.row_value, a.pdf_value or "—",
                      a.result, a.delta or a.detail)
    return verdicts


def _verdict(ident: Identification, cr: CleanRow, by_file: dict[str, ExtractedInvoice], policy: VouchPolicy,
             dups: dict, definition: Definition) -> Verdict:
    base = {"row_id": ident.row_id, "excel_row": ident.excel_row}
    if ident.status == "no_evidence":
        return Verdict(**base, verdict="no_evidence", reasons=[ident.reason],
                       steps=["Identification found no PDF for this row → ❓ no_evidence"])
    if ident.status == "needs_review":
        return Verdict(**base, verdict="needs_review", reasons=[f"which PDF is not settled: {ident.reason}"],
                       steps=["Identification needs review, so the claim is not compared yet → 👀 needs_review"])

    inv = by_file[ident.invoice_file]
    agreements = compare(cr, inv, policy.amount_tol, definition, defaults={"currency": policy.currency})
    checks = check_invoice(inv, policy, dups)
    differs = [f"{a.field} differs: row {a.row_value}, PDF {a.pdf_value}" + (f", {a.delta}" if a.delta else "")
               for a in agreements if a.result == "differs"]
    fails = [f"{c.name} failed: {c.detail}" for c in checks if c.status == "fail"]
    unknown = [f"{a.field} unverifiable: {a.detail}" for a in agreements if a.result == "unverifiable"]
    blocking = ("duplicate", "totals_reconcile")  # a double payment; a total nothing shows to add up
    if policy.line_items:  # the engagement asked for the item lines: unread is missing evidence, not a note
        blocking += ("lines_sum",)
    unknown += [f"{c.name} not checkable: {c.detail}" for c in checks
                if c.status == "not_checkable" and c.name in blocking]
    warns = [f"{c.name} warning: {c.detail}" for c in checks if c.status == "warn"]
    warns += [f"{c.name} not checkable: {c.detail}" for c in checks
              if c.status == "not_checkable" and c.name not in blocking]

    steps = [f"Linked to {inv.file}; compared {len(agreements)} claimed fields and ran {len(checks)} invoice checks",
             "Fields: " + ", ".join(f"{a.field} {a.result}" for a in agreements),
             "Checks: " + ", ".join(f"{c.name} {c.status}" for c in checks)]
    if differs or fails:
        verdict, reasons = "difference", differs + fails + unknown  # what could not be verified, too
        steps.append(f"{len(differs)} field(s) differ and {len(fails)} check(s) fail → ⚠️ difference")
    elif unknown:
        verdict, reasons = "needs_review", unknown
        steps.append("Nothing differs, but not everything could be verified → 👀 needs_review")
    else:
        verdict, reasons = "matched", []
        steps.append("Every claimed field agrees and no check fails → ✅ matched")
    if warns:
        steps.append("Warnings and checks that could not run (shown, not blocking): " + "; ".join(warns))
    return Verdict(**base, verdict=verdict, invoice_file=inv.file, agreements=agreements, checks=checks,
                   reasons=reasons + warns, steps=steps)
