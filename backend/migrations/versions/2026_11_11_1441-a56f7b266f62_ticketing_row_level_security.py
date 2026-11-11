"""ticketing row level security

Tenant isolation for customers, tickets, messages, events and ticket counters,
plus the customer-portal boundary *inside* a tenant:

- Staff (admin/agent members of the current organization) see all of its rows.
- A customer member sees only their own customer record, the tickets of that
  record, the non-internal messages on those tickets, and their "created" and
  "status_changed" events. Internal notes and assignment history stay hidden even
  from an unfiltered query.

Whether the caller is staff is derived from ``organization_members`` (not from a
session setting), so the policies follow role changes immediately. Helpers are
called through scalar subqueries, so they are evaluated once per statement rather
than once per row.

Revision ID: a56f7b266f62
Revises: d6c970f2eb03
Create Date: 2026-11-11 14:41:12.181252

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a56f7b266f62"
down_revision: str | Sequence[str] | None = "d6c970f2eb03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BYPASS = "app_rls_bypass()"
IN_ORG = "organization_id = app_current_organization_id()"
STAFF = "(SELECT app_is_staff())"
OWN_RECORD = "user_id = app_current_user_id()"
OWN_CUSTOMER = "customer_id IN (SELECT app_own_customer_ids())"
OWN_TICKET = (
    "ticket_id IN (SELECT id FROM tickets WHERE customer_id IN (SELECT app_own_customer_ids()))"
)
CUSTOMER_EVENTS = "type IN ('created', 'status_changed')"

TABLES = ("customers", "tickets", "ticket_messages", "ticket_events", "ticket_counters")
ACTIONS = ("select", "insert", "update", "delete")


def _policies(table: str, *, select: str, insert: str, update: str, delete: str) -> None:
    """One policy per command; each condition is scoped to the current tenant."""

    def scoped(condition: str) -> str:
        return f"{BYPASS} OR ({IN_ORG} AND ({condition}))"

    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({scoped(select)})")
    op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({scoped(insert)})")
    op.execute(
        f"CREATE POLICY {table}_update ON {table} FOR UPDATE "
        f"USING ({scoped(update)}) WITH CHECK ({scoped(update)})"
    )
    op.execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({scoped(delete)})")


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION app_is_staff() RETURNS boolean
        LANGUAGE sql STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM organization_members
                WHERE organization_id = app_current_organization_id()
                  AND user_id = app_current_user_id()
                  AND role IN ('admin', 'agent')
            )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_own_customer_ids() RETURNS SETOF uuid
        LANGUAGE sql STABLE
        AS $$
            SELECT id FROM customers
            WHERE organization_id = app_current_organization_id()
              AND user_id = app_current_user_id()
        $$
        """
    )

    # A customer may read and create their own record (created on their first
    # ticket); only staff edit or delete customers.
    own_or_staff = f"{STAFF} OR {OWN_RECORD}"
    _policies("customers", select=own_or_staff, insert=own_or_staff, update=STAFF, delete=STAFF)

    # Customers create tickets and move their own tickets' status (by replying);
    # the API restricts which fields they may change.
    own_tickets = f"{STAFF} OR {OWN_CUSTOMER}"
    _policies("tickets", select=own_tickets, insert=own_tickets, update=own_tickets, delete=STAFF)

    _policies(
        "ticket_messages",
        select=f"{STAFF} OR (NOT is_internal AND {OWN_TICKET})",
        insert=(
            f"{STAFF} OR (NOT is_internal AND {OWN_TICKET} AND author_id = app_current_user_id())"
        ),
        update=STAFF,
        delete=STAFF,
    )

    customer_events = f"{STAFF} OR ({CUSTOMER_EVENTS} AND {OWN_TICKET})"
    _policies(
        "ticket_events",
        select=customer_events,
        insert=customer_events,
        update=STAFF,
        delete=STAFF,
    )

    # Anyone in the organization may allocate a ticket number.
    _policies("ticket_counters", select="true", insert="true", update="true", delete=STAFF)


def downgrade() -> None:
    for table in reversed(TABLES):
        for action in ACTIONS:
            op.execute(f"DROP POLICY {table}_{action} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_own_customer_ids()")
    op.execute("DROP FUNCTION app_is_staff()")
