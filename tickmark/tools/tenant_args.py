"""--tenant / --engagement / --user for the tools: who acts, for which firm and client.

Stand-ins for the Warden-verified token the API will use. They default to TICKMARK_TENANT,
TICKMARK_ENGAGEMENT and the OS user, so you can set them once per terminal:

    export TICKMARK_TENANT=<firm uuid> TICKMARK_ENGAGEMENT=<engagement uuid>

List the ids with: python -m tools.tenants list
"""
import argparse
import getpass
import os

from tickmark.store import TenantContext


def add_tenant_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--tenant", default=os.environ.get("TICKMARK_TENANT"), help="audit firm id (TICKMARK_TENANT)")
    ap.add_argument("--engagement", default=os.environ.get("TICKMARK_ENGAGEMENT"),
                    help="client engagement id (TICKMARK_ENGAGEMENT)")
    ap.add_argument("--user", default=getpass.getuser(), help="who is acting (default: OS user)")


def tenant_context(ap: argparse.ArgumentParser, args: argparse.Namespace) -> TenantContext:
    if not (args.tenant and args.engagement):
        ap.error("--tenant and --engagement are required (or set TICKMARK_TENANT / TICKMARK_ENGAGEMENT); "
                 "see: python -m tools.tenants list")
    try:
        return TenantContext(args.tenant, args.engagement, args.user)
    except ValueError as e:
        ap.error(f"bad tenant or engagement id: {e}")
