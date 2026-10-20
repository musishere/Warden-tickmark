"""flexible input: any sheet layout, typed fields in JSONB, saved column mappings

Additive only. Stored evidence is never modified and the append-only triggers are never disabled:
  - sample_rows gains `raw` (the row's cells as received) and `fields` (mapped, typed values);
    the fixed invoice columns become nullable and are no longer written. A CHECK requires either
    the old form or the new one, so rows written before this migration stay exactly as recorded.
  - sample_issues gains `raw`; `raw_cells` becomes nullable.
  - new audit table `column_mappings` (which column holds which field, who confirmed it), with
    the same protections as every audit table: scope, composite foreign keys, row-level security,
    append-only trigger, app role SELECT + INSERT.
  - runs record the definition used and the mapping.
A dev database may instead simply be reset (alembic downgrade base && alembic upgrade head).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21 10:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tickmark_app"
SCOPE = ["tenant_id", "engagement_id"]
IN_SCOPE = "tenant_id = tickmark_current_tenant() AND engagement_id = tickmark_current_engagement()"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "column_mappings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("engagement_id", sa.UUID(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("headers", JSONB, nullable=False),
        sa.Column("headers_sha256", sa.Text(), nullable=False),
        sa.Column("columns", JSONB, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("notes", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("proposal", JSONB, nullable=True),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source in ('aliases', 'claude', 'person')", name="column_mappings_source"),
        sa.CheckConstraint("source = 'aliases' or confirmed_by is not null", name="column_mappings_confirmed"),
        sa.ForeignKeyConstraint(SCOPE, ["engagements.tenant_id", "engagements.id"], name="column_mappings_engagement_fk"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "engagement_id", "id", name="column_mappings_scoped_key"),
    )
    op.execute("CREATE TRIGGER column_mappings_append_only BEFORE UPDATE OR DELETE ON column_mappings "
               "FOR EACH ROW EXECUTE FUNCTION forbid_change()")
    op.execute("CREATE TRIGGER column_mappings_no_truncate BEFORE TRUNCATE ON column_mappings "
               "FOR EACH STATEMENT EXECUTE FUNCTION forbid_change()")
    op.execute("ALTER TABLE column_mappings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE column_mappings FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY column_mappings_in_scope ON column_mappings USING ({IN_SCOPE}) WITH CHECK ({IN_SCOPE})")
    op.execute(f"GRANT SELECT, INSERT ON column_mappings TO {APP_ROLE}")

    op.add_column("runs", sa.Column("definition", JSONB, nullable=True))
    op.add_column("runs", sa.Column("mapping_id", sa.UUID(), nullable=True))
    op.create_foreign_key("runs_mapping_id_fk", "runs", "column_mappings", [*SCOPE, "mapping_id"],
                          [*SCOPE, "id"])

    op.add_column("sample_rows", sa.Column("raw", JSONB, nullable=True))
    op.add_column("sample_rows", sa.Column("fields", JSONB, nullable=True))
    for col, type_ in (("vendor", sa.Text()), ("amount", sa.Numeric()), ("invoice_date", sa.Date()),
                       ("vendor_key", sa.Text()), ("raw_cells", JSONB)):
        op.alter_column("sample_rows", col, existing_type=type_, nullable=True)
    op.create_check_constraint(
        "sample_rows_old_or_new_form", "sample_rows",
        "(raw is not null and fields is not null) or "
        "(vendor is not null and amount is not null and invoice_date is not null and raw_cells is not null)")

    op.add_column("sample_issues", sa.Column("raw", JSONB, nullable=True))
    op.alter_column("sample_issues", "raw_cells", existing_type=JSONB, nullable=True)
    op.create_check_constraint("sample_issues_has_cells", "sample_issues", "raw is not null or raw_cells is not null")


def downgrade() -> None:
    """Only on a database with no new-form rows: old columns cannot become NOT NULL otherwise."""
    op.drop_constraint("sample_issues_has_cells", "sample_issues", type_="check")
    op.alter_column("sample_issues", "raw_cells", existing_type=JSONB, nullable=False)
    op.drop_column("sample_issues", "raw")
    op.drop_constraint("sample_rows_old_or_new_form", "sample_rows", type_="check")
    for col, type_ in (("vendor", sa.Text()), ("amount", sa.Numeric()), ("invoice_date", sa.Date()),
                       ("vendor_key", sa.Text()), ("raw_cells", JSONB)):
        op.alter_column("sample_rows", col, existing_type=type_, nullable=False)
    op.drop_column("sample_rows", "fields")
    op.drop_column("sample_rows", "raw")
    op.drop_constraint("runs_mapping_id_fk", "runs", type_="foreignkey")
    op.drop_column("runs", "mapping_id")
    op.drop_column("runs", "definition")
    op.execute(f"REVOKE ALL ON column_mappings FROM {APP_ROLE}")
    op.drop_table("column_mappings")  # its policy and triggers go with it
