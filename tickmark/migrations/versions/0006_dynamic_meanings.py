"""dynamic meanings and local-first PDF reading

  - column_mappings gains `meanings`: the meanings a client layout adds to the catalogue
    ({name: {type, labels, description}}). Existing mappings have none.
  - engagements gain `line_items`: check that an invoice's item lines add up (needs Azure to read
    the table); off unless the engagement turns it on.
  - run_documents gain `read_by` (local | azure) and `read_reason` (why Azure was needed).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 10:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("column_mappings", sa.Column("meanings", postgresql.JSONB(astext_type=sa.Text()),
                                               server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column("engagements", sa.Column("line_items", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("run_documents", sa.Column("read_by", sa.Text(), nullable=True))
    op.add_column("run_documents", sa.Column("read_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_documents", "read_reason")
    op.drop_column("run_documents", "read_by")
    op.drop_column("engagements", "line_items")
    op.drop_column("column_mappings", "meanings")
