"""ticket closed notification kind

Customers can close their own requests; the assignee is told with a new
notification kind, "ticket_closed". Enums are VARCHAR + CHECK (see str_enum_type),
so the allowed values are widened by hand.

Revision ID: 5c0fde8e1841
Revises: c5f9e1c07836
Create Date: 2026-11-12 10:40:36.696718

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c0fde8e1841"
down_revision: str | Sequence[str] | None = "c5f9e1c07836"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'ticket_assigned', 'customer_replied', 'ai_escalated', 'agent_replied'"
NEW_KINDS = KINDS + ", 'ticket_closed'"
TABLES = ("notifications", "notification_preferences")


def _set_kinds(kinds: str) -> None:
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_notification_kind"), table, type_="check")
        op.create_check_constraint("notification_kind", table, f"kind IN ({kinds})")


def upgrade() -> None:
    """Upgrade schema."""
    _set_kinds(NEW_KINDS)


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f"DELETE FROM {table} WHERE kind = 'ticket_closed'")
    _set_kinds(KINDS)
