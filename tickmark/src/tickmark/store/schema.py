"""Tickmark's audit tables. Append-only: a database trigger rejects UPDATE and DELETE on all of them
(see migrations/). A re-run is a new run; a human decision is a new review row.

Everything is linked by UUID and by SHA-256 of file content, never by file name.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def _id() -> Column:
    return Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _now(name: str) -> Column:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=func.now())


documents = Table(
    "documents", metadata,
    _id(),
    Column("sha256", Text, nullable=False, unique=True),
    Column("kind", Text, nullable=False),  # pdf | sample
    Column("size_bytes", BigInteger, nullable=False),
    Column("pages", Integer),
    Column("storage_path", Text, nullable=False),  # content-addressed copy in the evidence store
    _now("first_seen_at"),
    CheckConstraint("kind in ('pdf', 'sample')", name="documents_kind"),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="documents_sha256_hex"),
)

extractions = Table(
    "extractions", metadata,
    _id(),
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False),
    Column("extractor", Text, nullable=False),  # azure-docint | labels
    Column("model_id", Text, nullable=False, server_default=""),
    Column("api_version", Text),
    Column("raw_sha256", Text, nullable=False),  # hash of the raw result, to dedupe and to prove it
    Column("raw", JSONB, nullable=False),  # exactly what the extractor returned
    Column("fields", JSONB, nullable=False),  # parsed: name -> {value, confidence, snip}
    _now("created_at"),
    UniqueConstraint("document_id", "extractor", "model_id", "raw_sha256", name="extractions_once"),
)

runs = Table(
    "runs", metadata,
    _id(),
    Column("engagement_id", Text),  # Warden's engagement id; no cross-database foreign key
    Column("run_by", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    Column("status", Text, nullable=False),  # completed | failed
    Column("error", Text),
    Column("sample_document_id", UUID(as_uuid=True), ForeignKey("documents.id")),
    Column("sheet", Text),
    Column("matcher_version", Text, nullable=False),  # package version + git commit (+dirty)
    Column("thresholds", JSONB, nullable=False),
    Column("extractor", Text, nullable=False),
    Column("model_id", Text),
    CheckConstraint("status in ('completed', 'failed')", name="runs_status"),
    CheckConstraint("status = 'failed' or sample_document_id is not null", name="runs_completed_has_sample"),
)

run_documents = Table(
    "run_documents", metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False),
    Column("file_name", Text, nullable=False),  # as named in this run
    Column("extraction_id", UUID(as_uuid=True), ForeignKey("extractions.id"), nullable=False),
    # keyed by name: the same bytes may appear twice in one folder under two names
    PrimaryKeyConstraint("run_id", "file_name"),
)

sample_rows = Table(
    "sample_rows", metadata,
    _id(),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("row_id", Text, nullable=False),  # as the auditor wrote it, e.g. S-01
    Column("excel_row", Integer, nullable=False),
    Column("vendor", Text, nullable=False),
    Column("amount", Numeric, nullable=False),  # exact decimal as loaded, no float
    Column("invoice_date", Date, nullable=False),
    Column("reference", Text),
    Column("vendor_key", Text, nullable=False),
    Column("flags", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("raw_cells", JSONB, nullable=False),  # the Excel cells exactly as read
    UniqueConstraint("run_id", "excel_row", name="sample_rows_once"),
)

sample_issues = Table(
    "sample_issues", metadata,
    _id(),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("excel_row", Integer, nullable=False),
    Column("kind", Text, nullable=False),  # error | skipped
    Column("reason", Text, nullable=False),
    Column("raw_cells", JSONB, nullable=False),
    CheckConstraint("kind in ('error', 'skipped')", name="sample_issues_kind"),
    UniqueConstraint("run_id", "excel_row", name="sample_issues_once"),
)

match_results = Table(
    "match_results", metadata,
    _id(),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False),
    Column("sample_row_id", UUID(as_uuid=True), ForeignKey("sample_rows.id"), nullable=False, unique=True),
    Column("status", Text, nullable=False),  # linked | needs_review | no_evidence
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id")),
    Column("page", Integer),
    Column("evidence", JSONB, nullable=False),  # human-readable lines + snips
    Column("candidates", JSONB, nullable=False),  # each with document_id
    Column("reason", Text, nullable=False, server_default=""),
    Column("comparisons", JSONB, nullable=False),  # this row vs every PDF, field by field
    Column("steps", JSONB, nullable=False),  # the decision, in order
    CheckConstraint("status in ('linked', 'needs_review', 'no_evidence')", name="match_results_status"),
    CheckConstraint("(status = 'linked') = (document_id is not null)", name="match_results_linked_has_document"),
)

reviews = Table(
    "reviews", metadata,
    _id(),
    Column("match_result_id", UUID(as_uuid=True), ForeignKey("match_results.id"), nullable=False),
    Column("reviewer", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("chosen_document_id", UUID(as_uuid=True), ForeignKey("documents.id")),
    Column("note", Text, nullable=False, server_default=""),
    _now("decided_at"),
    CheckConstraint("decision in ('accept', 'choose_candidate', 'reject', 'requested_from_client')",
                    name="reviews_decision"),
    CheckConstraint("(decision = 'choose_candidate') = (chosen_document_id is not null)",
                    name="reviews_choice_has_document"),
)

Index("match_results_run", match_results.c.run_id)
Index("sample_rows_run", sample_rows.c.run_id)
Index("sample_issues_run", sample_issues.c.run_id)
Index("reviews_result", reviews.c.match_result_id, reviews.c.decided_at)
Index("runs_engagement", runs.c.engagement_id, runs.c.started_at)

AUDIT_TABLES = [t.name for t in metadata.sorted_tables]
