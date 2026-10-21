"""Tickmark's audit tables. Append-only: a database trigger rejects UPDATE and DELETE on all of them
(see migrations/). A re-run is a new run; a human decision is a new review row.

Multi-tenant: every audit row carries tenant_id (the audit firm) and engagement_id (the client
being audited). Postgres row-level security shows a session only the rows of the tenant and
engagement it set (see tickmark.store.context), and composite foreign keys make it impossible to
link rows across engagements. Files are de-duplicated per engagement only, so an ethical wall
cannot leak through storage.

Everything is linked by UUID and by SHA-256 of file content, never by file name.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()
SCOPE = ["tenant_id", "engagement_id"]


def _id() -> Column:
    return Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _now(name: str) -> Column:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=func.now())


def _scope(table: str) -> list:
    """tenant_id + engagement_id, pointing at a registered engagement of that tenant."""
    return [
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        Column("engagement_id", UUID(as_uuid=True), nullable=False),
        ForeignKeyConstraint(SCOPE, ["engagements.tenant_id", "engagements.id"], name=f"{table}_engagement_fk"),
    ]


def _scoped_key(table: str) -> UniqueConstraint:
    """(tenant_id, engagement_id, id): what child rows reference, so a link can't cross engagements."""
    return UniqueConstraint("tenant_id", "engagement_id", "id", name=f"{table}_scoped_key")


def _link(table: str, column: str, parent: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint([*SCOPE, column], [f"{parent}.tenant_id", f"{parent}.engagement_id", f"{parent}.id"],
                                name=f"{table}_{column}_fk")


# Registry: ids are Warden's. Written only by the owner role (provisioning); the app can only read.
tenants = Table(
    "tenants", metadata,
    _id(),
    Column("name", Text, nullable=False),
    Column("entra_tenant_id", Text, unique=True),
    _now("created_at"),
)

engagements = Table(
    "engagements", metadata,
    _id(),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False),
    Column("client_name", Text, nullable=False),
    _now("created_at"),
    Column("period_start", Date),  # the audit period (Warden's); invoice dates are checked against it
    Column("period_end", Date),
    Column("currency", Text),  # three-letter code: the amounts' currency when a row does not say
    Column("line_items", Boolean, nullable=False, server_default=false()),  # check item lines (needs Azure)
    UniqueConstraint("tenant_id", "id", name="engagements_scoped_key"),
    CheckConstraint("period_end >= period_start", name="engagements_period_order"),
    CheckConstraint("currency ~ '^[A-Z]{3}$'", name="engagements_currency_code"),
)

documents = Table(
    "documents", metadata,
    _id(),
    *_scope("documents"),
    Column("sha256", Text, nullable=False),
    Column("kind", Text, nullable=False),  # pdf | sample
    Column("size_bytes", BigInteger, nullable=False),
    Column("pages", Integer),
    Column("storage_path", Text, nullable=False),  # content-addressed copy in the evidence store
    _now("first_seen_at"),
    _scoped_key("documents"),
    UniqueConstraint("tenant_id", "engagement_id", "sha256", name="documents_once_per_engagement"),
    CheckConstraint("kind in ('pdf', 'sample')", name="documents_kind"),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="documents_sha256_hex"),
)

extractions = Table(
    "extractions", metadata,
    _id(),
    *_scope("extractions"),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("extractor", Text, nullable=False),  # azure-docint | labels
    Column("model_id", Text, nullable=False, server_default=""),
    Column("api_version", Text),
    Column("raw_sha256", Text, nullable=False),  # hash of the raw result, to dedupe and to prove it
    Column("raw", JSONB, nullable=False),  # exactly what the extractor returned
    Column("fields", JSONB, nullable=False),  # parsed: name -> {value, confidence, snip}
    _now("created_at"),
    Column("line_items", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    _scoped_key("extractions"),
    _link("extractions", "document_id", "documents"),
    UniqueConstraint("document_id", "extractor", "model_id", "raw_sha256", name="extractions_once"),
)

column_mappings = Table(
    "column_mappings", metadata,
    _id(),
    *_scope("column_mappings"),
    Column("definition", Text, nullable=False),  # e.g. invoice_vouching
    Column("definition_version", Integer, nullable=False),
    Column("headers", JSONB, nullable=False),  # the header row it was made for: [{col, header}]
    Column("headers_sha256", Text, nullable=False),
    Column("columns", JSONB, nullable=False),  # field -> column letter
    Column("meanings", JSONB, nullable=False, server_default=text("'{}'::jsonb")),  # the layout's own meanings
    Column("source", Text, nullable=False),  # aliases | claude | person
    Column("notes", JSONB, nullable=False, server_default=text("'{}'::jsonb")),  # field -> reason
    Column("proposal", JSONB),  # what was sent to and returned by the LLM, if it proposed
    Column("confirmed_by", Text),  # who confirmed it; null for header aliases
    _now("created_at"),
    _scoped_key("column_mappings"),
    CheckConstraint("source in ('aliases', 'claude', 'person')", name="column_mappings_source"),
    CheckConstraint("source = 'aliases' or confirmed_by is not null", name="column_mappings_confirmed"),
)

runs = Table(
    "runs", metadata,
    _id(),
    *_scope("runs"),
    Column("run_by", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    Column("status", Text, nullable=False),  # completed | failed
    Column("error", Text),
    Column("sample_document_id", UUID(as_uuid=True)),
    Column("sheet", Text),
    Column("matcher_version", Text, nullable=False),  # package version + git commit (+dirty)
    Column("thresholds", JSONB, nullable=False),
    Column("extractor", Text, nullable=False),
    Column("model_id", Text),
    Column("definition", JSONB),  # the audit test definition used (name, version, snapshot)
    Column("mapping_id", UUID(as_uuid=True)),  # which column mapping turned the sheet into fields
    _scoped_key("runs"),
    _link("runs", "sample_document_id", "documents"),
    _link("runs", "mapping_id", "column_mappings"),
    CheckConstraint("status in ('completed', 'failed')", name="runs_status"),
    CheckConstraint("status = 'failed' or sample_document_id is not null", name="runs_completed_has_sample"),
)

run_documents = Table(
    "run_documents", metadata,
    *_scope("run_documents"),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("file_name", Text, nullable=False),  # as named in this run
    Column("extraction_id", UUID(as_uuid=True), nullable=False),
    Column("read_by", Text),  # local | azure
    Column("read_reason", Text),  # why Azure was needed
    # keyed by name: the same bytes may appear twice in one folder under two names
    PrimaryKeyConstraint("run_id", "file_name"),
    _link("run_documents", "run_id", "runs"),
    _link("run_documents", "document_id", "documents"),
    _link("run_documents", "extraction_id", "extractions"),
)

sample_rows = Table(
    "sample_rows", metadata,
    _id(),
    *_scope("sample_rows"),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("row_id", Text, nullable=False),  # as the auditor wrote it, e.g. S-01
    Column("excel_row", Integer, nullable=False),
    # rows written before 0004 (fixed invoice columns; kept exactly as recorded, never rewritten)
    Column("vendor", Text),
    Column("amount", Numeric),  # exact decimal as loaded, no float
    Column("invoice_date", Date),
    Column("reference", Text),
    Column("raw_cells", JSONB),  # the Excel cells exactly as read (values only)
    # rows written since 0004: any sheet, any definition
    Column("raw", JSONB),  # the row's cells as received: [{col, header, value, kind}]
    Column("fields", JSONB),  # mapped, typed values: {name: {type, value, col}}; money as a string
    Column("vendor_key", Text),  # the party's matching key
    Column("flags", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    _scoped_key("sample_rows"),
    CheckConstraint("(raw is not null and fields is not null) or "
                    "(vendor is not null and amount is not null and invoice_date is not null and raw_cells is not null)",
                    name="sample_rows_old_or_new_form"),
    _link("sample_rows", "run_id", "runs"),
    UniqueConstraint("run_id", "excel_row", name="sample_rows_once"),
)

sample_issues = Table(
    "sample_issues", metadata,
    _id(),
    *_scope("sample_issues"),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("excel_row", Integer, nullable=False),
    Column("kind", Text, nullable=False),  # error | skipped
    Column("reason", Text, nullable=False),
    Column("raw_cells", JSONB),  # before 0004
    Column("raw", JSONB),  # since 0004: [{col, header, value, kind}]
    _link("sample_issues", "run_id", "runs"),
    CheckConstraint("raw is not null or raw_cells is not null", name="sample_issues_has_cells"),
    CheckConstraint("kind in ('error', 'skipped')", name="sample_issues_kind"),
    UniqueConstraint("run_id", "excel_row", name="sample_issues_once"),
)

match_results = Table(
    "match_results", metadata,
    _id(),
    *_scope("match_results"),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("sample_row_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("status", Text, nullable=False),  # linked | needs_review | no_evidence
    Column("document_id", UUID(as_uuid=True)),
    Column("page", Integer),
    Column("evidence", JSONB, nullable=False),  # human-readable lines + snips
    Column("candidates", JSONB, nullable=False),  # each with document_id
    Column("reason", Text, nullable=False, server_default=""),
    Column("comparisons", JSONB, nullable=False),  # this row vs every PDF, field by field
    Column("steps", JSONB, nullable=False),  # the decision, in order
    _scoped_key("match_results"),
    _link("match_results", "run_id", "runs"),
    _link("match_results", "sample_row_id", "sample_rows"),
    _link("match_results", "document_id", "documents"),
    CheckConstraint("status in ('linked', 'needs_review', 'no_evidence')", name="match_results_status"),
    CheckConstraint("(status = 'linked') = (document_id is not null)", name="match_results_linked_has_document"),
)

reviews = Table(
    "reviews", metadata,
    _id(),
    *_scope("reviews"),
    Column("match_result_id", UUID(as_uuid=True), nullable=False),
    Column("reviewer", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("chosen_document_id", UUID(as_uuid=True)),
    Column("note", Text, nullable=False, server_default=""),
    _now("decided_at"),
    _link("reviews", "match_result_id", "match_results"),
    _link("reviews", "chosen_document_id", "documents"),
    CheckConstraint("decision in ('accept', 'choose_candidate', 'reject', 'requested_from_client')",
                    name="reviews_decision"),
    CheckConstraint("(decision = 'choose_candidate') = (chosen_document_id is not null)",
                    name="reviews_choice_has_document"),
)

verdicts = Table(
    "verdicts", metadata,
    _id(),
    *_scope("verdicts"),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("match_result_id", UUID(as_uuid=True), nullable=False, unique=True),
    Column("verdict", Text, nullable=False),  # matched | difference | needs_review | no_evidence
    Column("document_id", UUID(as_uuid=True)),  # the PDF the claim was checked against
    Column("agreements", JSONB, nullable=False),  # each claimed field vs the PDF
    Column("checks", JSONB, nullable=False),  # the invoice's own checks
    Column("reasons", JSONB, nullable=False),
    Column("steps", JSONB, nullable=False),
    _link("verdicts", "run_id", "runs"),
    _link("verdicts", "match_result_id", "match_results"),
    _link("verdicts", "document_id", "documents"),
    CheckConstraint("verdict in ('matched', 'difference', 'needs_review', 'no_evidence')", name="verdicts_verdict"),
    # ✅/⚠️ compared a document; ❓ has none; 👀 may be linked but not fully verifiable (or not linked)
    CheckConstraint("(verdict not in ('matched', 'difference') or document_id is not null) and "
                    "(verdict <> 'no_evidence' or document_id is null)",
                    name="verdicts_checked_has_document"),
)

Index("match_results_run", match_results.c.run_id)
Index("verdicts_run", verdicts.c.run_id)
Index("sample_rows_run", sample_rows.c.run_id)
Index("sample_issues_run", sample_issues.c.run_id)
Index("reviews_result", reviews.c.match_result_id, reviews.c.decided_at)
Index("runs_engagement", runs.c.tenant_id, runs.c.engagement_id, runs.c.started_at)
Index("engagements_tenant", engagements.c.tenant_id)

REGISTRY_TABLES = ["tenants", "engagements"]
AUDIT_TABLES = ["documents", "extractions", "column_mappings", "runs", "run_documents", "sample_rows",
                "sample_issues", "match_results", "reviews", "verdicts"]
