"""knowledge base row level security

Documents and their chunks are the organization's internal knowledge: visible to
its staff (admins and agents) only; customers never read them directly. The
ingestion worker has no user; it runs as an explicit system operation
(``app_rls_bypass()``) scoped in code to one document.

Revision ID: 2df998e2425e
Revises: 41a10fdb432a
Create Date: 2026-11-11 15:42:39.110466

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2df998e2425e"
down_revision: str | Sequence[str] | None = "41a10fdb432a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("documents", "document_chunks")
STAFF_IN_ORG = (
    "app_rls_bypass() OR "
    "(organization_id = app_current_organization_id() AND (SELECT app_is_staff()))"
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_staff ON {table} FOR ALL "
            f"USING ({STAFF_IN_ORG}) WITH CHECK ({STAFF_IN_ORG})"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY {table}_staff ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
