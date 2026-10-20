"""multi tenant: tenant + engagement on every audit row, row-level security, app role

Every audit row gets tenant_id (audit firm) and engagement_id (client being audited), both Warden's
ids. Postgres row-level security shows a session only the rows of the tenant and engagement it set
with set_config('app.tenant_id' / 'app.engagement_id', ..., true). If nothing is set, nothing is
visible and nothing can be inserted (fail closed). Composite foreign keys make links across
engagements impossible. The app role tickmark_app gets SELECT + INSERT only.

Audit tables are append-only, so existing rows cannot be assigned a tenant: this migration refuses
to run if any audit table holds data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 20:49:39.899531

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tickmark_app"
AUDIT_TABLES = ["documents", "extractions", "runs", "run_documents", "sample_rows", "sample_issues",
                "match_results", "reviews"]
# tables other rows point at: they get a (tenant_id, engagement_id, id) key
SCOPED_KEYS = ["documents", "extractions", "runs", "sample_rows", "match_results"]
# (table, column, parent): each becomes a composite foreign key (tenant_id, engagement_id, column)
LINKS = [
    ("extractions", "document_id", "documents"),
    ("runs", "sample_document_id", "documents"),
    ("run_documents", "run_id", "runs"),
    ("run_documents", "document_id", "documents"),
    ("run_documents", "extraction_id", "extractions"),
    ("sample_rows", "run_id", "runs"),
    ("sample_issues", "run_id", "runs"),
    ("match_results", "run_id", "runs"),
    ("match_results", "sample_row_id", "sample_rows"),
    ("match_results", "document_id", "documents"),
    ("reviews", "match_result_id", "match_results"),
    ("reviews", "chosen_document_id", "documents"),
]
SCOPE = ["tenant_id", "engagement_id"]
IN_SCOPE = "tenant_id = tickmark_current_tenant() AND engagement_id = tickmark_current_engagement()"


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF {" OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in AUDIT_TABLES)} THEN
                RAISE EXCEPTION 'audit tables hold data: append-only rows cannot be assigned a tenant. '
                    'Reset a dev database (alembic downgrade base && alembic upgrade head).';
            END IF;
        END $$""")

    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("entra_tenant_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entra_tenant_id"),
    )
    op.create_table(
        "engagements",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("client_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="engagements_scoped_key"),
    )
    op.create_index("engagements_tenant", "engagements", ["tenant_id"])

    # 1. columns (tables are empty, see the guard above)
    for t in AUDIT_TABLES:
        op.add_column(t, sa.Column("tenant_id", sa.UUID(), nullable=False))
        if t != "runs":
            op.add_column(t, sa.Column("engagement_id", sa.UUID(), nullable=False))
    op.drop_index("runs_engagement", table_name="runs")
    op.alter_column("runs", "engagement_id", existing_type=sa.Text(), type_=sa.UUID(), nullable=False,
                    postgresql_using="engagement_id::uuid")
    op.create_index("runs_engagement", "runs", ["tenant_id", "engagement_id", "started_at"])

    # 2. replace the single-column links with scoped ones
    for t, col, _ in LINKS:
        op.drop_constraint(f"{t}_{col}_fkey", t, type_="foreignkey")
    op.drop_constraint("documents_sha256_key", "documents", type_="unique")
    op.create_unique_constraint("documents_once_per_engagement", "documents", ["tenant_id", "engagement_id", "sha256"])
    for t in SCOPED_KEYS:
        op.create_unique_constraint(f"{t}_scoped_key", t, ["tenant_id", "engagement_id", "id"])
    for t in AUDIT_TABLES:
        op.create_foreign_key(f"{t}_engagement_fk", t, "engagements", SCOPE, ["tenant_id", "id"])
    for t, col, parent in LINKS:
        op.create_foreign_key(f"{t}_{col}_fk", t, parent, [*SCOPE, col], [*SCOPE, "id"])

    # 3. row-level security: a session sees only the tenant + engagement it set; nothing if unset
    op.execute("""
        CREATE FUNCTION tickmark_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
            $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$""")
    op.execute("""
        CREATE FUNCTION tickmark_current_engagement() RETURNS uuid LANGUAGE sql STABLE AS
            $$ SELECT nullif(current_setting('app.engagement_id', true), '')::uuid $$""")
    for t in [*AUDIT_TABLES, "tenants", "engagements"]:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    for t in AUDIT_TABLES:
        op.execute(f"CREATE POLICY {t}_in_scope ON {t} USING ({IN_SCOPE}) WITH CHECK ({IN_SCOPE})")
    op.execute("CREATE POLICY tenants_own ON tenants FOR SELECT USING (id = tickmark_current_tenant())")
    op.execute("CREATE POLICY engagements_own ON engagements FOR SELECT "
               "USING (tenant_id = tickmark_current_tenant() AND id = tickmark_current_engagement())")

    # 4. the app role: read + append only. It is not the owner, so it cannot bypass any of the above.
    op.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$""")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON tenants, engagements TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(AUDIT_TABLES)} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {', '.join([*AUDIT_TABLES, 'tenants', 'engagements'])} FROM {APP_ROLE}")
    for t in AUDIT_TABLES:
        op.execute(f"DROP POLICY {t}_in_scope ON {t}")
    op.execute("DROP POLICY tenants_own ON tenants")
    op.execute("DROP POLICY engagements_own ON engagements")
    for t in AUDIT_TABLES:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION tickmark_current_tenant()")
    op.execute("DROP FUNCTION tickmark_current_engagement()")

    for t, col, _ in reversed(LINKS):
        op.drop_constraint(f"{t}_{col}_fk", t, type_="foreignkey")
    for t in AUDIT_TABLES:
        op.drop_constraint(f"{t}_engagement_fk", t, type_="foreignkey")
    for t in SCOPED_KEYS:
        op.drop_constraint(f"{t}_scoped_key", t, type_="unique")
    op.drop_constraint("documents_once_per_engagement", "documents", type_="unique")
    op.create_unique_constraint("documents_sha256_key", "documents", ["sha256"])
    for t, col, parent in LINKS:
        op.create_foreign_key(f"{t}_{col}_fkey", t, parent, [col], ["id"])

    op.drop_index("runs_engagement", table_name="runs")
    op.alter_column("runs", "engagement_id", existing_type=sa.UUID(), type_=sa.Text(), nullable=True)
    op.create_index("runs_engagement", "runs", ["engagement_id", "started_at"])
    for t in AUDIT_TABLES:
        op.drop_column(t, "tenant_id")
        if t != "runs":
            op.drop_column(t, "engagement_id")

    op.drop_index("engagements_tenant", table_name="engagements")
    op.drop_table("engagements")
    op.drop_table("tenants")
