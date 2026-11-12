"""sla monitor events and notifications

Module 10, phase 4: the SLA monitor records "sla_at_risk" and "sla_breached" ticket
events (staff only) and sends notifications of the same kinds. Enums are VARCHAR +
CHECK (see str_enum_type), so the allowed values are widened by hand.

Revision ID: 5c0f1ef48a59
Revises: 7bdcedc17a11
Create Date: 2026-11-12 13:26:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c0f1ef48a59"
down_revision: str | Sequence[str] | None = "7bdcedc17a11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = (
    "'created', 'status_changed', 'priority_changed', 'subject_changed', 'assigned', "
    "'unassigned', 'ai_escalated', 'category_changed'"
)
NEW_EVENTS = EVENTS + ", 'sla_at_risk', 'sla_breached'"
KINDS = "'ticket_assigned', 'customer_replied', 'ai_escalated', 'agent_replied', 'ticket_closed'"
NEW_KINDS = KINDS + ", 'sla_at_risk', 'sla_breached'"
NOTIFICATION_TABLES = ("notifications", "notification_preferences")


def _set(events: str, kinds: str) -> None:
    op.drop_constraint(op.f("ck_ticket_events_ticket_event_type"), "ticket_events", type_="check")
    op.create_check_constraint("ticket_event_type", "ticket_events", f"type IN ({events})")
    for table in NOTIFICATION_TABLES:
        op.drop_constraint(op.f(f"ck_{table}_notification_kind"), table, type_="check")
        op.create_check_constraint("notification_kind", table, f"kind IN ({kinds})")


def upgrade() -> None:
    """Upgrade schema."""
    _set(NEW_EVENTS, NEW_KINDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM ticket_events WHERE type IN ('sla_at_risk', 'sla_breached')")
    for table in NOTIFICATION_TABLES:
        op.execute(f"DELETE FROM {table} WHERE kind IN ('sla_at_risk', 'sla_breached')")
    _set(EVENTS, KINDS)
