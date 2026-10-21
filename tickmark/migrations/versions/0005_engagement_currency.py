"""engagement currency: the currency a row's amount is in when the row does not say

Engagements are a registry (not an audit table), like their audit period. Each run records the
currency it used in its thresholds (the vouch policy).

Also corrects verdicts_checked_has_document: a 👀 needs_review verdict may have a document (linked,
but a field or check could not be verified, e.g. no currency or no audit period). ✅/⚠️ still
require one and ❓ still has none. Only relaxes the rule: every existing row satisfies it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = ("(verdict not in ('matched', 'difference') or document_id is not null) and "
       "(verdict <> 'no_evidence' or document_id is null)")


def upgrade() -> None:
    op.add_column("engagements", sa.Column("currency", sa.Text(), nullable=True))
    op.create_check_constraint("engagements_currency_code", "engagements", "currency ~ '^[A-Z]{3}$'")
    op.drop_constraint("verdicts_checked_has_document", "verdicts", type_="check")
    op.create_check_constraint("verdicts_checked_has_document", "verdicts", NEW)


def downgrade() -> None:
    """Only on a database with no linked 👀 verdict: the old rule forbids them."""
    op.drop_constraint("verdicts_checked_has_document", "verdicts", type_="check")
    op.create_check_constraint("verdicts_checked_has_document", "verdicts", "(verdict in ('matched', 'difference')) = (document_id is not null)")
    op.drop_constraint("engagements_currency_code", "engagements", type_="check")
    op.drop_column("engagements", "currency")
