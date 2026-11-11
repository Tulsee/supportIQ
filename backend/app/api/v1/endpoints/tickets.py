"""Tickets of the organization in ``X-Organization-Id``.

Admins and agents work every ticket. Customers create tickets and see only their
own, with internal notes and assignment history hidden.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, EventBrokerDep, Pagination, TenantMembership
from app.models import TicketPriority, TicketStatus
from app.repositories.tickets import TicketFilters, TicketSortKey
from app.schemas.common import Page, error_responses
from app.schemas.tickets import (
    EventRead,
    MessageCreate,
    MessageRead,
    TicketCounts,
    TicketCreate,
    TicketRead,
    TicketUpdate,
)
from app.services.tickets import TicketService, allowed_statuses

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    responses=error_responses(400, 401, 403, 404, 409, 422),
)


@router.get("", summary="List tickets")
async def list_tickets(
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    page: Pagination,
    status_: Annotated[
        list[TicketStatus] | None, Query(alias="status", description="Any of these statuses.")
    ] = None,
    priority: Annotated[
        list[TicketPriority] | None, Query(description="Any of these priorities.")
    ] = None,
    assignee: Annotated[
        Literal["me", "unassigned"] | uuid.UUID | None,
        Query(description="`me`, `unassigned`, or a user id (staff only)."),
    ] = None,
    customer_id: uuid.UUID | None = None,
    search: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Ticket number (#42), customer name or email, or words in the "
            "subject or messages.",
        ),
    ] = None,
    sort: TicketSortKey = "-updated_at",
) -> Page[TicketRead]:
    """Newest activity first by default. Customers only ever get their own tickets."""
    filters = TicketFilters(
        statuses=status_ or (),
        priorities=priority or (),
        assignee=assignee,
        customer_id=customer_id,
        search=search.strip() if search and search.strip() else None,
        sort=sort,
    )
    tickets, total = await TicketService(session).list_tickets(
        member, user, filters, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[TicketRead.build(t, allowed_statuses(member, t)) for t in tickets],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/counts", summary="Ticket counts for the inbox (staff)")
async def ticket_counts(
    member: TenantMembership, user: CurrentUser, session: DbSession
) -> TicketCounts:
    by_status, unassigned, mine = await TicketService(session).counts(member, user)
    return TicketCounts(by_status=by_status, unassigned=unassigned, assigned_to_me=mine)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Open a ticket")
async def create_ticket(
    body: TicketCreate,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    events: EventBrokerDep,
) -> TicketRead:
    """Staff open tickets for a customer (`customer_id` required) and may set the
    priority and assignee. Customers open tickets for themselves."""
    ticket = await TicketService(session, events).create(member, user, body)
    return TicketRead.build(ticket, allowed_statuses(member, ticket))


@router.get("/{ticket_id}", summary="Get a ticket")
async def get_ticket(
    ticket_id: uuid.UUID, member: TenantMembership, user: CurrentUser, session: DbSession
) -> TicketRead:
    ticket = await TicketService(session).get(member, user, ticket_id)
    return TicketRead.build(ticket, allowed_statuses(member, ticket))


@router.patch("/{ticket_id}", summary="Update a ticket (staff)")
async def update_ticket(
    ticket_id: uuid.UUID,
    body: TicketUpdate,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    events: EventBrokerDep,
) -> TicketRead:
    """409 `invalid_status_transition` if the status change isn't allowed by the
    lifecycle; `details.allowed` lists the valid next statuses."""
    ticket = await TicketService(session, events).update(member, user, ticket_id, body)
    return TicketRead.build(ticket, allowed_statuses(member, ticket))


@router.get("/{ticket_id}/messages", summary="List a ticket's messages (oldest first)")
async def list_messages(
    ticket_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    page: Pagination,
) -> Page[MessageRead]:
    """Customers never receive internal notes."""
    messages, total = await TicketService(session).list_messages(
        member, user, ticket_id, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[MessageRead.build(m) for m in messages],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/{ticket_id}/messages", status_code=status.HTTP_201_CREATED, summary="Reply or add a note"
)
async def add_message(
    ticket_id: uuid.UUID,
    body: MessageCreate,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    events: EventBrokerDep,
) -> MessageRead:
    """Internal notes are staff only. A customer's reply moves a ticket that is
    waiting for them, or resolved, back to in progress. Closed tickets take no
    messages (409 `ticket_closed`)."""
    message = await TicketService(session, events).add_message(member, user, ticket_id, body)
    return MessageRead.build(message)


@router.get("/{ticket_id}/events", summary="A ticket's timeline (oldest first)")
async def list_events(
    ticket_id: uuid.UUID, member: TenantMembership, user: CurrentUser, session: DbSession
) -> list[EventRead]:
    """Customers see when the ticket was opened and its status changes."""
    events = await TicketService(session).list_events(member, user, ticket_id)
    return [EventRead.build(e) for e in events]
