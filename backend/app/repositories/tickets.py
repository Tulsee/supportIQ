import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, case, exists, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.security import utcnow
from app.models import (
    Customer,
    SlaClockState,
    TeamMember,
    Ticket,
    TicketCounter,
    TicketEvent,
    TicketEventType,
    TicketMessage,
    TicketPriority,
    TicketSlaClock,
    TicketStatus,
)
from app.models.ticket import SEARCH_CONFIG

ACTIVE_STATUSES = tuple(
    s for s in TicketStatus if s not in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
)
_PRIORITY_RANK = case(
    {
        TicketPriority.URGENT: 3,
        TicketPriority.HIGH: 2,
        TicketPriority.NORMAL: 1,
        TicketPriority.LOW: 0,
    },
    value=Ticket.priority,
)

TicketSortKey = Literal["-updated_at", "-created_at", "created_at", "-priority"]


@dataclass(frozen=True)
class TicketFilters:
    statuses: Sequence[TicketStatus] = ()
    priorities: Sequence[TicketPriority] = ()
    # "me", "unassigned", a user id, or None for anyone.
    assignee: Literal["me", "unassigned"] | uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    # Tickets of any team the current user belongs to.
    my_teams: bool = False
    # An open SLA clock that's due soon (warning time passed) or past its deadline.
    sla: Literal["at_risk", "breached"] | None = None
    now: datetime | None = None  # for the SLA filter (defaults to the current time)
    search: str | None = None
    sort: TicketSortKey = "-updated_at"
    # Customers: restrict to the customer records linked to this user.
    only_for_user_id: uuid.UUID | None = field(default=None)


def _sla_condition(kind: Literal["at_risk", "breached"], now: datetime) -> ColumnElement[bool]:
    """An unmet clock of the ticket is breached, or due soon (and not yet breached). The
    same rules as ``app.domain.sla.status``."""
    clock = TicketSlaClock
    running = clock.state == SlaClockState.RUNNING
    if kind == "breached":
        state = (clock.state.in_([SlaClockState.RUNNING, SlaClockState.PAUSED])) & (
            clock.breached_at.is_not(None) | (running & (clock.due_at <= now))
        )
    else:
        state = (
            running & clock.breached_at.is_(None) & (clock.warn_at <= now) & (clock.due_at > now)
        )
    return exists().where(clock.ticket_id == Ticket.id, state)


def _search_condition(search: str) -> ColumnElement[bool]:
    """Ticket number (#42), customer name/email, or full text of subject/messages.

    Message matches run under the caller's row-level security, so a customer's
    search never matches text that exists only in internal notes."""
    query = func.websearch_to_tsquery(literal_column(f"'{SEARCH_CONFIG}'::regconfig"), search)
    conditions: list[ColumnElement[bool]] = [
        Ticket.search_vector.bool_op("@@")(query),
        exists().where(
            TicketMessage.ticket_id == Ticket.id,
            TicketMessage.search_vector.bool_op("@@")(query),
        ),
        Ticket.customer_id.in_(
            select(Customer.id).where(
                or_(
                    Customer.name.icontains(search, autoescape=True),
                    Customer.email.icontains(search, autoescape=True),
                )
            )
        ),
    ]
    number = search.strip().removeprefix("#")
    if number.isdigit() and len(number) < 10:
        conditions.append(Ticket.number == int(number))
    return or_(*conditions)


class TicketRepository:
    """Tickets, their messages and events. Every query is scoped to one organization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, *objects: Ticket | TicketMessage | TicketEvent) -> None:
        self.session.add_all(objects)

    async def next_number(self, organization_id: uuid.UUID) -> int:
        """Allocate the organization's next ticket number. A single upsert, so
        concurrent creates serialize on the counter row and never share a number."""
        stmt = insert(TicketCounter).values(organization_id=organization_id, last_number=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=[TicketCounter.organization_id],
            set_={"last_number": TicketCounter.last_number + 1},
        ).returning(TicketCounter.last_number)
        return (await self.session.execute(stmt)).scalar_one()

    async def get(
        self, organization_id: uuid.UUID, ticket_id: uuid.UUID, *, for_update: bool = False
    ) -> Ticket | None:
        stmt = (
            select(Ticket)
            .options(
                joinedload(Ticket.customer),
                joinedload(Ticket.assignee),
                joinedload(Ticket.category),
                joinedload(Ticket.team),
            )
            .where(Ticket.id == ticket_id, Ticket.organization_id == organization_id)
        )
        if for_update:
            stmt = stmt.with_for_update(of=Ticket)
        return await self.session.scalar(stmt)

    def _conditions(
        self, organization_id: uuid.UUID, filters: TicketFilters, current_user_id: uuid.UUID
    ) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [Ticket.organization_id == organization_id]
        if filters.only_for_user_id is not None:
            conditions.append(
                Ticket.customer_id.in_(
                    select(Customer.id).where(
                        Customer.organization_id == organization_id,
                        Customer.user_id == filters.only_for_user_id,
                    )
                )
            )
        if filters.statuses:
            conditions.append(Ticket.status.in_(filters.statuses))
        if filters.priorities:
            conditions.append(Ticket.priority.in_(filters.priorities))
        if filters.assignee == "me":
            conditions.append(Ticket.assignee_id == current_user_id)
        elif filters.assignee == "unassigned":
            conditions.append(Ticket.assignee_id.is_(None))
        elif filters.assignee is not None:
            conditions.append(Ticket.assignee_id == filters.assignee)
        if filters.customer_id is not None:
            conditions.append(Ticket.customer_id == filters.customer_id)
        if filters.category_id:
            conditions.append(Ticket.category_id == filters.category_id)
        if filters.team_id:
            conditions.append(Ticket.team_id == filters.team_id)
        if filters.my_teams:
            conditions.append(
                Ticket.team_id.in_(
                    select(TeamMember.team_id).where(
                        TeamMember.organization_id == organization_id,
                        TeamMember.user_id == current_user_id,
                    )
                )
            )
        if filters.sla is not None:
            conditions.append(_sla_condition(filters.sla, filters.now or utcnow()))
        if filters.search:
            conditions.append(_search_condition(filters.search))
        return conditions

    async def find(
        self,
        organization_id: uuid.UUID,
        filters: TicketFilters,
        *,
        current_user_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[Ticket], int]:
        conditions = self._conditions(organization_id, filters, current_user_id)
        order = {
            "-updated_at": (Ticket.updated_at.desc(),),
            "-created_at": (Ticket.created_at.desc(),),
            "created_at": (Ticket.created_at.asc(),),
            "-priority": (_PRIORITY_RANK.desc(), Ticket.updated_at.desc()),
        }[filters.sort]
        stmt = (
            select(Ticket)
            .options(
                joinedload(Ticket.customer),
                joinedload(Ticket.assignee),
                joinedload(Ticket.category),
                joinedload(Ticket.team),
            )
            .where(*conditions)
            .order_by(*order, Ticket.id)
            .limit(limit)
            .offset(offset)
        )
        tickets = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count()).select_from(Ticket).where(*conditions)
        )
        return tickets, total or 0

    async def counts(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[dict[TicketStatus, int], int, int]:
        in_org = Ticket.organization_id == organization_id
        by_status = {status: 0 for status in TicketStatus}
        rows = await self.session.execute(
            select(Ticket.status, func.count()).where(in_org).group_by(Ticket.status)
        )
        for status, count in rows.all():
            by_status[status] = count
        active = Ticket.status.in_(ACTIVE_STATUSES)
        unassigned = await self.session.scalar(
            select(func.count()).where(in_org, active, Ticket.assignee_id.is_(None))
        )
        mine = await self.session.scalar(
            select(func.count()).where(in_org, active, Ticket.assignee_id == user_id)
        )
        return by_status, unassigned or 0, mine or 0

    async def active_assigned_to(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Ticket]:
        """Open tickets assigned to the user, locked for update (for unassignment)."""
        stmt = (
            select(Ticket)
            .options(
                joinedload(Ticket.customer),
                joinedload(Ticket.assignee),
                joinedload(Ticket.category),
                joinedload(Ticket.team),
            )
            .where(
                Ticket.organization_id == organization_id,
                Ticket.assignee_id == user_id,
                Ticket.status != TicketStatus.CLOSED,
            )
            .with_for_update(of=Ticket)
        )
        return list((await self.session.scalars(stmt)).all())

    # --- Messages and events -----------------------------------------------------------

    async def list_messages(
        self, ticket_id: uuid.UUID, *, include_internal: bool, limit: int, offset: int
    ) -> tuple[list[TicketMessage], int]:
        conditions: list[ColumnElement[bool]] = [TicketMessage.ticket_id == ticket_id]
        if not include_internal:
            conditions.append(TicketMessage.is_internal.is_(False))
        stmt = (
            select(TicketMessage)
            .options(joinedload(TicketMessage.author))
            .where(*conditions)
            .order_by(TicketMessage.seq)
            .limit(limit)
            .offset(offset)
        )
        messages = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count()).select_from(TicketMessage).where(*conditions)
        )
        return messages, total or 0

    async def list_events(
        self, ticket_id: uuid.UUID, *, types: Sequence[TicketEventType] | None
    ) -> list[TicketEvent]:
        stmt = (
            select(TicketEvent)
            .options(joinedload(TicketEvent.actor))
            .where(TicketEvent.ticket_id == ticket_id)
            .order_by(TicketEvent.seq)
            .limit(500)
        )
        if types is not None:
            stmt = stmt.where(TicketEvent.type.in_(types))
        return list((await self.session.scalars(stmt)).all())
