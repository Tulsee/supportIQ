"""ai runs row level security

AI runs and their retrieval results reveal knowledge-base content and how it was
ranked: staff (admins and agents) of the organization only, like the documents.

Revision ID: 52dfb05b0af2
Revises: 841be7629c99
Create Date: 2026-11-11 21:45:21.522447

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "52dfb05b0af2"
down_revision: str | Sequence[str] | None = "841be7629c99"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("ai_runs", "retrieval_results")
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
