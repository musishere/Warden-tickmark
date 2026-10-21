"""Register audit firms (tenants) and their client engagements. Uses the owner database role.

    python -m tools.tenants add-tenant "Firm A" [--entra-tenant-id <tid>] [--id <warden uuid>]
    python -m tools.tenants add-engagement <tenant id> "Coke" [--id <warden uuid>] [--period 2026-01-01:2026-12-31] \
        [--currency EUR]
    python -m tools.tenants set-period <engagement id> 2026-01-01:2026-12-31
    python -m tools.tenants set-currency <engagement id> EUR
    python -m tools.tenants list

Warden is the source of truth; pass its ids with --id so both systems agree.
"""
import argparse
from itertools import groupby

from tickmark.store.provision import (
    add_engagement,
    add_tenant,
    list_tenants,
    set_currency,
    set_period,
)
from tickmark.vouch import parse_period


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("add-tenant")
    t.add_argument("name")
    t.add_argument("--entra-tenant-id")
    t.add_argument("--id")
    e = sub.add_parser("add-engagement")
    e.add_argument("tenant_id")
    e.add_argument("client_name")
    e.add_argument("--id")
    e.add_argument("--period", help="audit period YYYY-MM-DD:YYYY-MM-DD")
    e.add_argument("--currency", help="three-letter code of the client's amounts, e.g. EUR")
    sp = sub.add_parser("set-period")
    sp.add_argument("engagement_id")
    sp.add_argument("period", help="YYYY-MM-DD:YYYY-MM-DD")
    sc = sub.add_parser("set-currency")
    sc.add_argument("engagement_id")
    sc.add_argument("currency", help="three-letter code, e.g. EUR")
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "add-tenant":
        print(add_tenant(args.name, args.entra_tenant_id, args.id))
    elif args.cmd == "add-engagement":
        print(add_engagement(args.tenant_id, args.client_name, args.id,
                             parse_period(args.period) if args.period else None, args.currency))
    elif args.cmd == "set-period":
        set_period(args.engagement_id, parse_period(args.period))
        print(f"engagement {args.engagement_id}: audit period {args.period}")
    elif args.cmd == "set-currency":
        set_currency(args.engagement_id, args.currency)
        print(f"engagement {args.engagement_id}: currency {args.currency.upper()}")
    else:
        rows = list_tenants()
        if not rows:
            print("no tenants yet: python -m tools.tenants add-tenant \"Firm A\"")
        for (tid, name, entra), group in groupby(rows, key=lambda r: (r["id"], r["name"], r["entra_tenant_id"])):
            print(f"{name}  tenant {tid}" + (f"  (entra {entra})" if entra else ""))
            for r in group:
                if r["engagement_id"]:
                    period = f"  period {r['period_start']} … {r['period_end']}" if r["period_start"] else "  (no period)"
                    currency = f"  currency {r['currency']}" if r["currency"] else "  (no currency)"
                    print(f"    {r['client_name']:<20} engagement {r['engagement_id']}{period}{currency}")


if __name__ == "__main__":
    main()
