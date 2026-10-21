"""Does the linked PDF back the row's claim? One verdict per row, decided in code.

  identification no_evidence  -> ❓ no_evidence
  identification needs_review -> 👀 needs_review (not compared: which PDF it is is not settled)
  linked:
    any field differs or any check fails   -> ⚠️ difference (every field and check listed)
    else anything unverifiable / not_checkable -> 👀 needs_review (never ✅ on missing evidence)
    else                                    -> ✅ matched
A check that warns (e.g. the number is also used by another vendor) is shown but does not block ✅.
"""
from tickmark.models import CleanRow, ExtractedInvoice, Identification, Verdict
from tickmark.vouch.compare import compare
from tickmark.vouch.invoice_checks import check_invoice, duplicate_map
from tickmark.vouch.policy import VouchPolicy

MARK = {"matched": "✅", "difference": "⚠️", "needs_review": "👀", "no_evidence": "❓"}


def vouch(identifications: list[Identification], rows: list[CleanRow], invoices: list[ExtractedInvoice],
          policy: VouchPolicy) -> list[Verdict]:
    by_row = {cr.row.excel_row: cr for cr in rows}
    by_file = {inv.file: inv for inv in invoices}
    dups = duplicate_map(invoices)
    return [_verdict(ident, by_row[ident.excel_row], by_file, policy, dups) for ident in identifications]


def _verdict(ident: Identification, cr: CleanRow, by_file: dict[str, ExtractedInvoice], policy: VouchPolicy,
             dups: dict) -> Verdict:
    base = {"row_id": ident.row_id, "excel_row": ident.excel_row}
    if ident.status == "no_evidence":
        return Verdict(**base, verdict="no_evidence", reasons=[ident.reason],
                       steps=["Identification found no PDF for this row → ❓ no_evidence"])
    if ident.status == "needs_review":
        return Verdict(**base, verdict="needs_review", reasons=[f"which PDF is not settled: {ident.reason}"],
                       steps=["Identification needs review, so the claim is not compared yet → 👀 needs_review"])

    inv = by_file[ident.invoice_file]
    agreements = compare(cr, inv, policy.amount_tol, defaults={"currency": policy.currency})
    checks = check_invoice(inv, policy, dups)
    differs = [f"{a.field} differs: row {a.row_value}, PDF {a.pdf_value}" + (f", {a.delta}" if a.delta else "")
               for a in agreements if a.result == "differs"]
    fails = [f"{c.name} failed: {c.detail}" for c in checks if c.status == "fail"]
    unknown = [f"{a.field} unverifiable: {a.detail}" for a in agreements if a.result == "unverifiable"]
    unknown += [f"{c.name} not checkable: {c.detail}" for c in checks if c.status == "not_checkable"]
    warns = [f"{c.name} warning: {c.detail}" for c in checks if c.status == "warn"]

    steps = [f"Linked to {inv.file}; compared {len(agreements)} claimed fields and ran {len(checks)} invoice checks",
             "Fields: " + ", ".join(f"{a.field} {a.result}" for a in agreements),
             "Checks: " + ", ".join(f"{c.name} {c.status}" for c in checks)]
    if differs or fails:
        verdict, reasons = "difference", differs + fails
        steps.append(f"{len(differs)} field(s) differ and {len(fails)} check(s) fail → ⚠️ difference")
    elif unknown:
        verdict, reasons = "needs_review", unknown
        steps.append("Nothing differs, but not everything could be verified → 👀 needs_review")
    else:
        verdict, reasons = "matched", []
        steps.append("Every claimed field agrees and every check passes → ✅ matched")
    if warns:
        steps.append("Warnings (shown, not blocking): " + "; ".join(warns))
    return Verdict(**base, verdict=verdict, invoice_file=inv.file, agreements=agreements, checks=checks,
                   reasons=reasons + warns, steps=steps)
