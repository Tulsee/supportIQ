"""Tickets: lifecycle, assignment, messages, and the timeline.

Lifecycle (the only status changes allowed)::

    OPEN -> ASSIGNED -> IN_PROGRESS <-> WAITING_FOR_CUSTOMER
                        IN_PROGRESS  -> RESOLVED -> CLOSED
                        RESOLVED     -> IN_PROGRESS (reopen)

ASSIGNED is reached by assigning someone to an OPEN ticket, and unassigning an
ASSIGNED ticket returns it to OPEN, so "assigned" always means someone owns it. A
customer replying to a ticket that waits for them, or to a resolved ticket, moves
it back to IN_PROGRESS. CLOSED is final.
"""

import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.security import utcnow
from app.models import (
    CUSTOMER_VISIBLE_EVENTS,
    MessageAuthorType,
    NotificationKind,
    OrganizationMember,
    Team,
    Ticket,
    TicketCategory,
    TicketEvent,
    TicketEventType,
    TicketMessage,
    TicketPriority,
    TicketStatus,
    User,
)
from app.repositories.customers import CustomerRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.tickets import TicketFilters, TicketRepository
from app.schemas.tickets import MessageCreate, TicketCreate, TicketUpdate
from app.services.authorization import STAFF_ROLES, require_role
from app.services.customers import CustomerService
from app.services.notifications import NewNotification, NotificationService
from app.services.sla_clocks import SlaClockService
from app.websocket.broker import EventBroker, EventType
from app.websocket.broker import TicketEvent as RealtimeEvent

S = TicketStatus
# Any open ticket can be closed (resolved ones, but also spam, duplicates, or requests
# the customer no longer needs). Closing is final.
TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    S.OPEN: frozenset({S.ASSIGNED, S.CLOSED}),
    S.ASSIGNED: frozenset({S.IN_PROGRESS, S.CLOSED}),
    S.IN_PROGRESS: frozenset({S.WAITING_FOR_CUSTOMER, S.RESOLVED, S.CLOSED}),
    S.WAITING_FOR_CUSTOMER: frozenset({S.IN_PROGRESS, S.CLOSED}),
    S.RESOLVED: frozenset({S.IN_PROGRESS, S.CLOSED}),
    S.CLOSED: frozenset(),
}
# Statuses a customer's reply moves back to IN_PROGRESS.
_REOPENED_BY_CUSTOMER_REPLY = (S.WAITING_FOR_CUSTOMER, S.RESOLVED)

TICKET_CLOSED = "This ticket is closed. Open a new ticket to continue."


def _label(status: TicketStatus) -> str:
    return status.value.replace("_", " ")


def _person(user: User | None) -> dict[str, str] | None:
    return {"id": str(user.id), "name": user.full_name} if user else None


def _category(category: TicketCategory | None) -> dict[str, str] | None:
    return {"id": str(category.id), "name": category.name} if category else None


def _team(team: Team | None) -> dict[str, str] | None:
    return {"id": str(team.id), "name": team.name} if team else None


def is_staff(member: OrganizationMember) -> bool:
    return member.role in STAFF_ROLES


def allowed_statuses(member: OrganizationMember, ticket: Ticket) -> list[TicketStatus]:
    """Status choices to offer this member. ASSIGNED is excluded: it is reached by
    assigning someone, not by picking it."""
    if not is_staff(member):
        return []
    allowed = TRANSITIONS[ticket.status]
    return [status for status in TicketStatus if status in allowed and status != S.ASSIGNED]


class TicketService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventBroker | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.events = events  # real-time notifications, published after each commit
        # People to tell (the bell, email): written with the change, sent after commit.
        self.notifications = notifications
        self.sla = SlaClockService(session)
        self.tickets = TicketRepository(session)
        self.customers = CustomerRepository(session)
        self.orgs = OrganizationRepository(session)

    async def _notify(self, kind: EventType, ticket: Ticket, *, staff_only: bool = False) -> None:
        if self.events is not None:
            await self.events.publish(
                RealtimeEvent.build(
                    kind,
                    organization_id=ticket.organization_id,
                    ticket_id=ticket.id,
                    customer_user_id=ticket.customer.user_id,
                    staff_only=staff_only,
                )
            )

    # --- Reading --------------------------------------------------------------------

    async def list_tickets(
        self,
        member: OrganizationMember,
        user: User,
        filters: TicketFilters,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]:
        if not is_staff(member):
            # Customers only ever see their own tickets (RLS enforces this too).
            filters = replace(
                filters, only_for_user_id=user.id, assignee=None, category_id=None, team_id=None
            )
        return await self.tickets.find(
            member.organization_id, filters, current_user_id=user.id, limit=limit, offset=offset
        )

    async def counts(
        self, member: OrganizationMember, user: User
    ) -> tuple[dict[TicketStatus, int], int, int]:
        require_role(member, *STAFF_ROLES)
        return await self.tickets.counts(member.organization_id, user.id)

    async def get(
        self,
        member: OrganizationMember,
        user: User,
        ticket_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> Ticket:
        ticket = await self.tickets.get(member.organization_id, ticket_id, for_update=for_update)
        if ticket is None or (not is_staff(member) and ticket.customer.user_id != user.id):
            raise NotFoundError("Ticket not found")
        return ticket

    async def list_messages(
        self,
        member: OrganizationMember,
        user: User,
        ticket_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[TicketMessage], int]:
        ticket = await self.get(member, user, ticket_id)
        return await self.tickets.list_messages(
            ticket.id, include_internal=is_staff(member), limit=limit, offset=offset
        )

    async def list_events(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID
    ) -> list[TicketEvent]:
        ticket = await self.get(member, user, ticket_id)
        types = None if is_staff(member) else CUSTOMER_VISIBLE_EVENTS
        return await self.tickets.list_events(ticket.id, types=types)

    # --- Writing --------------------------------------------------------------------

    async def create(self, member: OrganizationMember, user: User, data: TicketCreate) -> Ticket:
        if is_staff(member):
            if data.customer_id is None:
                raise AppError("Choose the customer this ticket is for", code="customer_required")
            customer = await self.customers.get(member.organization_id, data.customer_id)
            if customer is None:
                raise AppError("Customer not found in this organization", code="invalid_customer")
            author_type = MessageAuthorType.AGENT
        else:
            if (
                data.customer_id
                or data.priority
                or data.assignee_id
                or data.category_id
                or data.team_id
            ):
                raise PermissionDeniedError("Customers can only set a ticket's subject and message")
            customer = await CustomerService(self.session).ensure_for_member(member, user)
            author_type = MessageAuthorType.CUSTOMER

        ticket = Ticket(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            number=await self.tickets.next_number(member.organization_id),
            subject=data.subject,
            status=S.OPEN,
            priority=data.priority or TicketPriority.NORMAL,
            created_by_id=user.id,
            resolved_at=None,
            closed_at=None,
        )
        ticket.customer = customer
        ticket.assignee = None
        ticket.category = None
        ticket.team = None
        message = TicketMessage(
            organization_id=member.organization_id,
            author_id=user.id,
            author_type=author_type,
            body=data.message,
            is_internal=False,
        )
        message.ticket = ticket
        self.tickets.add(ticket, message)
        self._record(ticket, user.id, TicketEventType.CREATED, {})
        if data.category_id is not None:
            await self._set_category(ticket, user.id, data.category_id)
        if data.team_id is not None:
            await self._set_team(ticket, user.id, data.team_id)
        if data.assignee_id is not None:
            await self._assign(ticket, user.id, data.assignee_id)
        await self.sla.start(ticket)
        await self.session.commit()
        await self._notify("ticket.created", ticket)
        await self._send_notifications()
        return ticket

    async def update(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID, data: TicketUpdate
    ) -> Ticket:
        require_role(member, *STAFF_ROLES)
        ticket = await self.get(member, user, ticket_id, for_update=True)
        if ticket.status == S.CLOSED:
            raise ConflictError("Closed tickets can't be changed", code="ticket_closed")
        fields = data.model_fields_set
        before = (ticket.status, ticket.subject)

        if "subject" in fields and data.subject is not None and data.subject != ticket.subject:
            self._record(
                ticket,
                user.id,
                TicketEventType.SUBJECT_CHANGED,
                {"from": ticket.subject, "to": data.subject},
            )
            ticket.subject = data.subject
        if "priority" in fields and data.priority is not None and data.priority != ticket.priority:
            self._record(
                ticket,
                user.id,
                TicketEventType.PRIORITY_CHANGED,
                {"from": ticket.priority, "to": data.priority},
            )
            ticket.priority = data.priority
            await self.sla.on_priority_change(ticket)
        # Assignment first: assigning an OPEN ticket makes it ASSIGNED, from which
        # a status in the same request (e.g. IN_PROGRESS) can follow.
        if "assignee_id" in fields:
            await self._assign(ticket, user.id, data.assignee_id)
        if "category_id" in fields:
            await self._set_category(ticket, user.id, data.category_id)
        if "team_id" in fields:
            await self._set_team(ticket, user.id, data.team_id)
        if "status" in fields and data.status is not None and data.status != ticket.status:
            await self._transition(ticket, user.id, data.status)

        await self.session.commit()
        # Customers see status and subject; priority and assignment are staff-only.
        customer_visible = (ticket.status, ticket.subject) != before
        await self._notify("ticket.updated", ticket, staff_only=not customer_visible)
        await self._send_notifications()
        return ticket

    async def add_message(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID, data: MessageCreate
    ) -> TicketMessage:
        staff = is_staff(member)
        if data.is_internal and not staff:
            raise PermissionDeniedError("Only staff can add internal notes")
        ticket = await self.get(member, user, ticket_id, for_update=True)
        if ticket.status == S.CLOSED:
            raise ConflictError(TICKET_CLOSED, code="ticket_closed")

        message = TicketMessage(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            author_id=user.id,
            author_type=MessageAuthorType.AGENT if staff else MessageAuthorType.CUSTOMER,
            body=data.body,
            is_internal=data.is_internal,
        )
        message.ticket = ticket
        message.author = user
        self.tickets.add(message)
        await self._notify_reply(ticket, message, user)
        if staff and not data.is_internal:
            await self.sla.on_first_response(ticket)
        if not staff and ticket.status in _REOPENED_BY_CUSTOMER_REPLY:
            await self._set_status(ticket, user.id, S.IN_PROGRESS)
        # New activity sorts the ticket to the top. clock_timestamp(), not now():
        # the moment of the reply, not the start of its transaction.
        ticket.updated_at = func.clock_timestamp()
        await self.session.commit()
        # An internal note must not even signal its existence to the customer.
        await self._notify("message.created", ticket, staff_only=data.is_internal)
        await self._send_notifications()
        return message

    async def close(self, member: OrganizationMember, user: User, ticket_id: uuid.UUID) -> Ticket:
        """Close a ticket for good: staff any ticket, customers their own requests
        (when they no longer need help). Closed tickets take no more replies."""
        ticket = await self.get(member, user, ticket_id, for_update=True)
        if ticket.status == S.CLOSED:
            raise ConflictError(TICKET_CLOSED, code="ticket_closed")
        await self._set_status(ticket, user.id, S.CLOSED)
        if (
            self.notifications is not None
            and not is_staff(member)
            and ticket.assignee_id is not None
        ):
            await self.notifications.add(
                ticket.organization_id,
                [
                    NewNotification(
                        user_id=ticket.assignee_id,
                        kind=NotificationKind.TICKET_CLOSED,
                        title=f"{ticket.customer.name} closed #{ticket.number}",
                        body=ticket.subject,
                        ticket_id=ticket.id,
                        dedupe_key=f"ticket_closed:{ticket.id}",
                    )
                ],
                actor_id=user.id,
            )
        ticket.updated_at = func.clock_timestamp()
        await self.session.commit()
        await self._notify("ticket.updated", ticket)
        await self._send_notifications()
        return ticket

    async def apply_routing(
        self,
        ticket: Ticket,
        actor_id: uuid.UUID | None,
        *,
        team_id: uuid.UUID,
        assignee_id: uuid.UUID | None,
        details: dict[str, Any],
    ) -> bool:
        """Carry out a routing decision on a locked ticket: its team, and the agent if
        one was picked. ``details`` (decision and rule) go into the staff-only events;
        without an actor the changes show as automatic. Doesn't commit. Returns whether
        customers can see a change (the status)."""
        before = ticket.status
        automatic = {"automatic": True} if actor_id is None else {}
        await self._set_team(ticket, actor_id, team_id, {**details, **automatic})
        if assignee_id is not None:
            await self._assign(
                ticket,
                actor_id,
                assignee_id,
                extra={**details, **automatic},
                status_extra=automatic or None,
            )
        return ticket.status != before

    async def publish_change(self, ticket: Ticket, *, customer_visible: bool) -> None:
        """After the commit: refresh open views and send the notifications."""
        await self._notify("ticket.updated", ticket, staff_only=not customer_visible)
        await self._send_notifications()

    async def unassign_member(self, actor: OrganizationMember, user_id: uuid.UUID) -> int:
        """Return a departing (or demoted) member's open tickets to the queue.
        Does not commit; the caller's membership change does."""
        tickets = await self.tickets.active_assigned_to(actor.organization_id, user_id)
        for ticket in tickets:
            await self._assign(ticket, actor.user_id, None)
        return len(tickets)

    # --- Helpers --------------------------------------------------------------------

    async def _set_category(
        self, ticket: Ticket, actor_id: uuid.UUID, category_id: uuid.UUID | None
    ) -> None:
        if category_id == ticket.category_id:
            return
        category = None
        if category_id is not None:
            category = await self.session.get(TicketCategory, category_id)
            if (
                category is None
                or category.organization_id != ticket.organization_id
                or not category.is_active
            ):
                raise AppError(
                    "Choose an active category of this organization", code="invalid_category"
                )
        previous = ticket.category
        ticket.category = category
        self._record(
            ticket,
            actor_id,
            TicketEventType.CATEGORY_CHANGED,
            {"from": _category(previous), "to": _category(category)},
        )

    async def _set_team(
        self,
        ticket: Ticket,
        actor_id: uuid.UUID | None,
        team_id: uuid.UUID | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if team_id == ticket.team_id:
            return
        team = None
        if team_id is not None:
            team = await self.session.get(Team, team_id)
            if team is None or team.organization_id != ticket.organization_id or not team.is_active:
                raise AppError("Choose an active team of this organization", code="invalid_team")
        previous = ticket.team
        ticket.team = team
        self._record(
            ticket,
            actor_id,
            TicketEventType.TEAM_CHANGED,
            {"from": _team(previous), "to": _team(team), **(extra or {})},
        )

    async def _notify_reply(self, ticket: Ticket, message: TicketMessage, author: User) -> None:
        """A customer reply tells the assignee; a public staff reply tells the
        customer (if they have an account). Internal notes tell nobody."""
        if self.notifications is None or message.is_internal:
            return
        if message.author_type == MessageAuthorType.CUSTOMER:
            if ticket.assignee_id is None:
                return
            item = NewNotification(
                user_id=ticket.assignee_id,
                kind=NotificationKind.CUSTOMER_REPLIED,
                title=f"{ticket.customer.name} replied on #{ticket.number}",
                body=message.body[:300],
                ticket_id=ticket.id,
                dedupe_key=f"customer_replied:{message.id}",
            )
        else:
            if ticket.customer.user_id is None:
                return
            item = NewNotification(
                user_id=ticket.customer.user_id,
                kind=NotificationKind.AGENT_REPLIED,
                title=f"New reply on your request #{ticket.number}",
                body=ticket.subject,
                ticket_id=ticket.id,
                dedupe_key=f"agent_replied:{message.id}",
            )
        await self.notifications.add(ticket.organization_id, [item], actor_id=author.id)

    async def _send_notifications(self) -> None:
        if self.notifications is not None:
            await self.notifications.flush()

    async def _assign(
        self,
        ticket: Ticket,
        actor_id: uuid.UUID | None,
        assignee_id: uuid.UUID | None,
        *,
        extra: dict[str, Any] | None = None,
        status_extra: dict[str, Any] | None = None,
    ) -> None:
        """``extra`` goes into the assignment event (staff only), ``status_extra`` into
        the status change it may cause (customers see that one)."""
        if assignee_id == ticket.assignee_id:
            return
        previous = ticket.assignee
        if assignee_id is None:
            ticket.assignee = None
            self._record(ticket, actor_id, TicketEventType.UNASSIGNED, {"from": _person(previous)})
            if ticket.status == S.ASSIGNED:
                await self._set_status(ticket, actor_id, S.OPEN)  # back to the queue
            return
        membership = await self.orgs.get_membership_with_user(ticket.organization_id, assignee_id)
        if membership is None or membership.role not in STAFF_ROLES:
            raise AppError(
                "Tickets can only be assigned to admins and agents of this organization",
                code="invalid_assignee",
            )
        ticket.assignee = membership.user
        self._record(
            ticket,
            actor_id,
            TicketEventType.ASSIGNED,
            {"from": _person(previous), "to": _person(membership.user), **(extra or {})},
        )
        if self.notifications is not None:
            await self.notifications.add(
                ticket.organization_id,
                [
                    NewNotification(
                        user_id=membership.user.id,
                        kind=NotificationKind.TICKET_ASSIGNED,
                        title=f"#{ticket.number} was assigned to you",
                        body=ticket.subject,
                        ticket_id=ticket.id,
                        # Every assignment is news, even back to the same person.
                        dedupe_key=f"ticket_assigned:{ticket.id}:{uuid.uuid4()}",
                    )
                ],
                actor_id=actor_id,
            )
        if ticket.status == S.OPEN:
            await self._set_status(ticket, actor_id, S.ASSIGNED, status_extra)

    async def _transition(self, ticket: Ticket, actor_id: uuid.UUID, to: TicketStatus) -> None:
        if to == S.ASSIGNED:
            raise ConflictError(
                "Assign the ticket to someone to mark it assigned",
                code="invalid_status_transition",
            )
        allowed = TRANSITIONS[ticket.status]
        if to not in allowed:
            raise ConflictError(
                f"A ticket can't move from {_label(ticket.status)} to {_label(to)}",
                code="invalid_status_transition",
                details={
                    "from": ticket.status,
                    "to": to,
                    "allowed": [s for s in TicketStatus if s in allowed],
                },
            )
        await self._set_status(ticket, actor_id, to)

    async def _set_status(
        self,
        ticket: Ticket,
        actor_id: uuid.UUID | None,
        to: TicketStatus,
        extra: dict[str, Any] | None = None,
    ) -> None:
        previous = ticket.status
        ticket.status = to
        if to == S.RESOLVED:
            ticket.resolved_at = utcnow()
        elif previous == S.RESOLVED and to == S.IN_PROGRESS:
            ticket.resolved_at = None  # reopened
        if to == S.CLOSED:
            ticket.closed_at = utcnow()
        self._record(
            ticket,
            actor_id,
            TicketEventType.STATUS_CHANGED,
            {"from": previous, "to": to, **(extra or {})},
        )
        await self.sla.on_status_change(ticket, previous, to, actor_id)

    def _record(
        self,
        ticket: Ticket,
        actor_id: uuid.UUID | None,
        event_type: TicketEventType,
        data: dict[str, Any],
    ) -> None:
        event = TicketEvent(
            organization_id=ticket.organization_id,
            actor_id=actor_id,
            type=event_type,
            data=data,
        )
        event.ticket = ticket
        self.tickets.add(event)
