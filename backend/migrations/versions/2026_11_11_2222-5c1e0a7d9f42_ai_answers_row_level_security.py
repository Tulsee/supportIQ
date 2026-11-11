"""ai answers row level security

Answers and their citations quote knowledge-base content: staff (admins and
agents) of the organization only, like the runs they belong to.

Revision ID: 5c1e0a7d9f42
Revises: bf36489bdcba
Create Date: 2026-11-11 22:22:10.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c1e0a7d9f42"
down_revision: str | Sequence[str] | None = "bf36489bdcba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("ai_answers", "citations")
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
