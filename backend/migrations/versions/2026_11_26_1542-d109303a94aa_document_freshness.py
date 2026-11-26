"""document freshness

Module 13, last part: an owner and a review date per document (with an optional
interval for the next date after a review), who reviewed it last and when, and the
"document_review_due" notification kind, and an index on citations by document (for
citation counts). The documents table keeps its RLS policies.

Revision ID: d109303a94aa
Revises: 633543d9f22f
Create Date: 2026-11-26 15:42:23.931658

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d109303a94aa"
down_revision: str | Sequence[str] | None = "633543d9f22f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "'ticket_assigned', 'customer_replied', 'ai_escalated', 'agent_replied', 'ticket_closed', "
    "'sla_at_risk', 'sla_breached', 'routing_no_agent'"
)
NEW_KINDS = KINDS + ", 'document_review_due'"
NOTIFICATION_TABLES = ("notifications", "notification_preferences")


def _set_kinds(kinds: str) -> None:
    for table in NOTIFICATION_TABLES:
        op.drop_constraint(op.f(f"ck_{table}_notification_kind"), table, type_="check")
        op.create_check_constraint("notification_kind", table, f"kind IN ({kinds})")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("review_due_on", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("review_interval_days", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("reviewed_by_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        "review_interval_days_positive", "documents", "review_interval_days > 0"
    )
    op.create_index(
        "ix_documents_review_due_on",
        "documents",
        ["review_due_on"],
        unique=False,
        postgresql_where=sa.text("review_due_on IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_documents_reviewed_by_id_users"),
        "documents",
        "users",
        ["reviewed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_documents_owner_id_users"),
        "documents",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_citations_document_id"), "citations", ["document_id"], unique=False)
    _set_kinds(NEW_KINDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM notifications WHERE kind = 'document_review_due'")
    op.execute("DELETE FROM notification_preferences WHERE kind = 'document_review_due'")
    _set_kinds(KINDS)
    op.drop_index(op.f("ix_citations_document_id"), table_name="citations")
    op.drop_constraint(op.f("fk_documents_owner_id_users"), "documents", type_="foreignkey")
    op.drop_constraint(op.f("fk_documents_reviewed_by_id_users"), "documents", type_="foreignkey")
    op.drop_index(
        "ix_documents_review_due_on",
        table_name="documents",
        postgresql_where=sa.text("review_due_on IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("ck_documents_review_interval_days_positive"), "documents", type_="check"
    )
    op.drop_column("documents", "reviewed_by_id")
    op.drop_column("documents", "reviewed_at")
    op.drop_column("documents", "review_interval_days")
    op.drop_column("documents", "review_due_on")
    op.drop_column("documents", "owner_id")
