"""engagement VAT rates: the rates (in %) its invoices may use, e.g. '0,7,19'

Engagements are a registry, not an audit table. Each run records the rates it used (its vouch policy).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("engagements", sa.Column("vat_rates", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("engagements", "vat_rates")
