"""Tickmark as an agent: vouch one engagement for a user, with every step through Warden.

    python -m tickmark.agent --engagement <id> --token <the user's sign-in token> [--period A:B]

Tickmark is a plain MCP client here (mcp_client.py): it connects to Warden's MCP address for the
engagement with the user's sign-in token, the same way Claude Code or any stock agent does.
Warden makes the pass itself, from the token: this user, this agent (the token says which app it
was issued to), this engagement.
1. list_documents
2. run_vouch
Warden says no at any step (wall, not a member, unknown agent, revoked, Warden down) -> stop, exit 1.
Every step, allowed or not, is in Warden's audit log (GET /audit?engagement=).

The sign-in token comes from the user (in dev: `dotnet run --project src/Warden.Api -- dev-token sara tickmark`:
issued to Tickmark, so Warden knows which agent it is; with Entra, the agent's own sign-in).
"""
import argparse
import sys

from tickmark.config import settings
from tickmark.mcp_client import McpClient, Refused


def vouch(mcp: McpClient, period: str | None, currency: str | None) -> dict:
    docs = mcp.call("list_documents", {})["documents"]
    print(f"{len(docs)} documents: {', '.join(d['name'] for d in docs)}")
    return mcp.call("run_vouch", {"period": period, "currency": currency})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engagement", required=True, help="Warden engagement id")
    ap.add_argument("--token", required=True, help="the user's sign-in token")
    ap.add_argument("--period", help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--currency")
    args = ap.parse_args()
    try:
        with McpClient(f"{settings.warden_url}/mcp/engagements/{args.engagement}", args.token) as mcp:
            result = vouch(mcp, args.period, args.currency)
    except Refused as e:
        print(f"Warden said no, stopping: {e}", file=sys.stderr)
        return 1
    for v in result["verdicts"]:
        why = f"  ({'; '.join(v['reasons'])})" if v["reasons"] else ""
        print(f"{v['row_id']:>6} {v['mark']}  {v['invoice_file'] or '-'}{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
