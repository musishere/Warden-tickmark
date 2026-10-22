"""Tickmark as an agent: vouch one engagement for a user, with every step through Warden.

    python -m tickmark.agent --engagement <id> --token <the user's sign-in token> [--period A:B]

1. trade the user's sign-in token for a pass: documents.read + vouch.run on that engagement only
2. list_documents through Warden's gate
3. run_vouch through Warden's gate
Warden says no at any step (wall, not a member, revoked, Warden down) -> stop, exit code 1.
Every step, allowed or not, is in Warden's audit log (GET /audit?engagement=).

The sign-in token comes from the user (in dev: `dotnet run --project src/Warden.Api -- dev-token sara`).
"""
import argparse
import sys

from tickmark.config import settings
from tickmark.warden_client import WardenClient, WardenDenied


def vouch(client: WardenClient, engagement: str, period: str | None, currency: str | None) -> dict:
    client.exchange_token("documents.read vouch.run", engagement)
    docs = client.invoke("list_documents", {})["documents"]
    print(f"{len(docs)} documents: {', '.join(d['name'] for d in docs)}")
    return client.invoke("run_vouch", {"period": period, "currency": currency})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engagement", required=True, help="Warden engagement id")
    ap.add_argument("--token", required=True, help="the user's sign-in token")
    ap.add_argument("--period", help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--currency")
    args = ap.parse_args()
    try:
        result = vouch(WardenClient(settings.warden_url, args.token), args.engagement, args.period, args.currency)
    except WardenDenied as e:
        print(f"Warden said no, stopping: {e}", file=sys.stderr)
        return 1
    for v in result["verdicts"]:
        why = f"  ({'; '.join(v['reasons'])})" if v["reasons"] else ""
        print(f"{v['row_id']:>6} {v['mark']}  {v['invoice_file'] or '-'}{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
