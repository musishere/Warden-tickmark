"""verdicts: does the linked PDF back each row's claim; audit period per engagement; line items

New audit table `verdicts` (one per match result), with the same protections as every audit table:
tenant + engagement scope, composite foreign keys, row-level security, append-only trigger, app role
SELECT + INSERT only. Engagements get their audit period; extractions keep the line items read.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20 21:40:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tickmark_app"
SCOPE = ["tenant_id", "engagement_id"]
IN_SCOPE = "tenant_id = tickmark_current_tenant() AND engagement_id = tickmark_current_engagement()"


def upgrade() -> None:
    op.add_column("engagements", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("engagements", sa.Column("period_end", sa.Date(), nullable=True))
    op.create_check_constraint("engagements_period_order", "engagements", "period_end >= period_start")
    op.add_column("extractions", sa.Column("line_items", postgresql.JSONB(astext_type=sa.Text()),
                                           server_default=sa.text("'[]'::jsonb"), nullable=False))

    op.create_table(
        "verdicts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("engagement_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("match_result_id", sa.UUID(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("agreements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("(verdict in ('matched', 'difference')) = (document_id is not null)",
                           name="verdicts_checked_has_document"),
        sa.CheckConstraint("verdict in ('matched', 'difference', 'needs_review', 'no_evidence')",
                           name="verdicts_verdict"),
        sa.ForeignKeyConstraint(SCOPE, ["engagements.tenant_id", "engagements.id"], name="verdicts_engagement_fk"),
        sa.ForeignKeyConstraint([*SCOPE, "run_id"], ["runs.tenant_id", "runs.engagement_id", "runs.id"],
                                name="verdicts_run_id_fk"),
        sa.ForeignKeyConstraint([*SCOPE, "match_result_id"],
                                ["match_results.tenant_id", "match_results.engagement_id", "match_results.id"],
                                name="verdicts_match_result_id_fk"),
        sa.ForeignKeyConstraint([*SCOPE, "document_id"],
                                ["documents.tenant_id", "documents.engagement_id", "documents.id"],
                                name="verdicts_document_id_fk"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_result_id"),
    )
    op.create_index("verdicts_run", "verdicts", ["run_id"])

    # the same protections as every audit table (0001: append-only, 0002: row-level security + app role)
    op.execute("CREATE TRIGGER verdicts_append_only BEFORE UPDATE OR DELETE ON verdicts "
               "FOR EACH ROW EXECUTE FUNCTION forbid_change()")
    op.execute("CREATE TRIGGER verdicts_no_truncate BEFORE TRUNCATE ON verdicts "
               "FOR EACH STATEMENT EXECUTE FUNCTION forbid_change()")
    op.execute("ALTER TABLE verdicts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verdicts FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY verdicts_in_scope ON verdicts USING ({IN_SCOPE}) WITH CHECK ({IN_SCOPE})")
    op.execute(f"GRANT SELECT, INSERT ON verdicts TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON verdicts FROM {APP_ROLE}")
    op.drop_index("verdicts_run", table_name="verdicts")
    op.drop_table("verdicts")  # drops its policy and triggers with it
    op.drop_column("extractions", "line_items")
    op.drop_constraint("engagements_period_order", "engagements", type_="check")
    op.drop_column("engagements", "period_end")
    op.drop_column("engagements", "period_start")
