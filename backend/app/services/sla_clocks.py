"""SLA clocks on tickets (Module 10, phase 3).

A ticket gets a first-response and a resolution clock when it opens, if service
levels are set up and a policy covers its priority. ``TicketService`` calls the hooks
below inside its own transaction:

- ``start``: when a ticket opens (or gains a covered priority).
- ``on_first_response``: the first public staff message after the opening one.
- ``on_status_change``: resolved meets the resolution clock; waiting for the customer
  pauses it and leaving that status resumes it; reopening starts a new resolution
  cycle; closing cancels whatever isn't met yet.
- ``on_priority_change``: re-targets unmet clocks with the new priority's policy.

A clock's deadline is ``started_at`` plus ``target_minutes + paused_minutes`` of
business time in the calendar it started with. Clocks are staff-only data, but
customers' actions move them too, so the hooks run under the RLS bypass.
"""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from app.core.security import utcnow
from app.domain.business_time import (
    Calendar,
    business_minutes_between,
    calculate_deadline,
    calendar_from_snapshot,
    calendar_snapshot,
    subtract_business_minutes,
)
from app.models import (
    SlaClockKind,
    SlaClockState,
    SlaPolicy,
    Ticket,
    TicketSlaClock,
    TicketSlaPause,
    TicketStatus,
)
from app.services.sla import SlaConfigService

K, ST, S = SlaClockKind, SlaClockState, TicketStatus
PAUSED_FOR_CUSTOMER = "waiting_for_customer"  # pause reason


def _targets(policy: SlaPolicy) -> dict[SlaClockKind, int]:
    return {
        K.FIRST_RESPONSE: policy.first_response_minutes,
        K.RESOLUTION: policy.resolution_minutes,
    }


def _open(clock: TicketSlaClock) -> bool:
    return clock.met_at is None and clock.state != ST.CANCELLED


class SlaClockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Reading -------------------------------------------------------------------

    async def clocks(self, ticket_id: uuid.UUID) -> list[TicketSlaClock]:
        rows = await self.session.scalars(
            select(TicketSlaClock)
            .where(TicketSlaClock.ticket_id == ticket_id)
            .order_by(TicketSlaClock.kind, TicketSlaClock.cycle)
        )
        return list(rows.all())

    async def current(self, ticket_id: uuid.UUID) -> dict[SlaClockKind, TicketSlaClock]:
        """The latest cycle of each kind."""
        latest: dict[SlaClockKind, TicketSlaClock] = {}
        for clock in await self.clocks(ticket_id):
            latest[clock.kind] = clock  # ordered by cycle
        return latest

    # --- Hooks (inside the caller's transaction) --------------------------------------

    async def start(self, ticket: Ticket) -> None:
        async with rls_bypass(self.session):
            await self._start(ticket, utcnow())

    async def on_first_response(self, ticket: Ticket) -> None:
        async with rls_bypass(self.session):
            clock = (await self.current(ticket.id)).get(K.FIRST_RESPONSE)
            if clock is not None and _open(clock):
                await self._meet(clock, utcnow())

    async def on_status_change(
        self,
        ticket: Ticket,
        previous: TicketStatus,
        to: TicketStatus,
        actor_id: uuid.UUID | None,
    ) -> None:
        async with rls_bypass(self.session):
            now = utcnow()
            clocks = await self.current(ticket.id)
            if to == S.CLOSED:
                for clock in clocks.values():
                    if _open(clock):
                        await self._cancel(clock, now)
                return
            resolution = clocks.get(K.RESOLUTION)
            if resolution is None:
                return
            if previous == S.WAITING_FOR_CUSTOMER and resolution.state == ST.PAUSED:
                await self._resume(resolution, now)
            if to == S.WAITING_FOR_CUSTOMER and resolution.state == ST.RUNNING:
                self._pause(resolution, now, PAUSED_FOR_CUSTOMER, actor_id)
            elif to == S.RESOLVED and _open(resolution):
                await self._meet(resolution, now)
            elif previous == S.RESOLVED and to == S.IN_PROGRESS and resolution.met_at is not None:
                self._next_cycle(resolution, now)

    async def on_priority_change(self, ticket: Ticket) -> None:
        async with rls_bypass(self.session):
            now = utcnow()
            clocks = await self.current(ticket.id)
            if not clocks:  # no SLA so far: maybe the new priority has one
                await self._start(ticket, now)
                return
            policy = await SlaConfigService(self.session).policy_for(
                ticket.organization_id, ticket.priority
            )
            for clock in clocks.values():
                if not _open(clock):
                    continue
                if policy is None:
                    await self._cancel(clock, now)
                    continue
                clock.policy_id = policy.id
                clock.policy_name = policy.name
                clock.policy_version = policy.version
                clock.target_minutes = _targets(policy)[clock.kind]
                clock.warning_minutes = policy.warning_minutes
                self._schedule(clock, calendar_from_snapshot(clock.calendar_snapshot))

    # --- Steps ---------------------------------------------------------------------------

    async def _start(self, ticket: Ticket, now: datetime) -> None:
        config = SlaConfigService(self.session)
        calendar = await config.calendar(ticket.organization_id)
        if calendar is None:
            return
        policy = await config.policy_for(ticket.organization_id, ticket.priority)
        if policy is None:
            return
        cal = calendar.to_calendar()
        for kind, target in _targets(policy).items():
            clock = TicketSlaClock(
                id=uuid.uuid4(),
                organization_id=ticket.organization_id,
                ticket_id=ticket.id,
                kind=kind,
                cycle=1,
                policy_id=policy.id,
                policy_name=policy.name,
                policy_version=policy.version,
                target_minutes=target,
                warning_minutes=policy.warning_minutes,
                calendar_snapshot=calendar_snapshot(cal),
                state=ST.RUNNING,
                started_at=now,
                paused_minutes=0,
            )
            self._schedule(clock, cal)
            self.session.add(clock)

    def _next_cycle(self, previous: TicketSlaClock, now: datetime) -> None:
        """A reopened ticket: a new resolution clock with the same target, from now."""
        clock = TicketSlaClock(
            id=uuid.uuid4(),
            organization_id=previous.organization_id,
            ticket_id=previous.ticket_id,
            kind=previous.kind,
            cycle=previous.cycle + 1,
            policy_id=previous.policy_id,
            policy_name=previous.policy_name,
            policy_version=previous.policy_version,
            target_minutes=previous.target_minutes,
            warning_minutes=previous.warning_minutes,
            calendar_snapshot=previous.calendar_snapshot,
            state=ST.RUNNING,
            started_at=now,
            paused_minutes=0,
        )
        self._schedule(clock, calendar_from_snapshot(previous.calendar_snapshot))
        self.session.add(clock)

    @staticmethod
    def _schedule(clock: TicketSlaClock, calendar: Calendar) -> None:
        """(Re)compute the deadline and warning time. A breached deadline stays put."""
        if clock.breached_at is not None:
            return
        due = calculate_deadline(
            clock.started_at, clock.target_minutes + clock.paused_minutes, calendar
        )
        warning = subtract_business_minutes(due, clock.warning_minutes, calendar)
        clock.due_at = due
        clock.warn_at = max(warning, clock.started_at)

    async def _meet(self, clock: TicketSlaClock, now: datetime) -> None:
        if clock.state == ST.PAUSED:
            await self._resume(clock, now)
        if clock.breached_at is None and now > clock.due_at:
            clock.breached_at = clock.due_at  # late: keep the breach on record
        clock.met_at = now
        clock.state = ST.MET

    async def _cancel(self, clock: TicketSlaClock, now: datetime) -> None:
        if clock.state == ST.PAUSED:
            await self._end_pause(clock, now)
            clock.paused_since = None
        clock.state = ST.CANCELLED
        clock.cancelled_at = now

    def _pause(
        self, clock: TicketSlaClock, now: datetime, reason: str, actor_id: uuid.UUID | None
    ) -> None:
        if clock.breached_at is None and now >= clock.due_at:
            clock.breached_at = clock.due_at  # it ran out before the pause
        clock.state = ST.PAUSED
        clock.paused_since = now
        self.session.add(
            TicketSlaPause(
                organization_id=clock.organization_id,
                clock_id=clock.id,
                reason=reason,
                actor_id=actor_id,
                started_at=now,
            )
        )

    async def _resume(self, clock: TicketSlaClock, now: datetime) -> None:
        calendar = calendar_from_snapshot(clock.calendar_snapshot)
        if clock.paused_since is not None:
            clock.paused_minutes += business_minutes_between(clock.paused_since, now, calendar)
        clock.paused_since = None
        clock.state = ST.RUNNING
        await self._end_pause(clock, now)
        self._schedule(clock, calendar)

    async def _end_pause(self, clock: TicketSlaClock, now: datetime) -> None:
        await self.session.execute(
            update(TicketSlaPause)
            .where(TicketSlaPause.clock_id == clock.id, TicketSlaPause.ended_at.is_(None))
            .values(ended_at=now)
        )
