"""Register audit firms (tenants) and their client engagements. Uses the owner database role.

    python -m tools.tenants add-tenant "Firm A" [--entra-tenant-id <tid>] [--id <warden uuid>]
    python -m tools.tenants add-engagement <tenant id> "Coke" [--id <warden uuid>]
    python -m tools.tenants list

Warden is the source of truth; pass its ids with --id so both systems agree.
"""
import argparse
from itertools import groupby

from tickmark.store.provision import add_engagement, add_tenant, list_tenants


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
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "add-tenant":
        print(add_tenant(args.name, args.entra_tenant_id, args.id))
    elif args.cmd == "add-engagement":
        print(add_engagement(args.tenant_id, args.client_name, args.id))
    else:
        rows = list_tenants()
        if not rows:
            print("no tenants yet: python -m tools.tenants add-tenant \"Firm A\"")
        for (tid, name, entra), group in groupby(rows, key=lambda r: (r["id"], r["name"], r["entra_tenant_id"])):
            print(f"{name}  tenant {tid}" + (f"  (entra {entra})" if entra else ""))
            for r in group:
                if r["engagement_id"]:
                    print(f"    {r['client_name']:<20} engagement {r['engagement_id']}")


if __name__ == "__main__":
    main()
