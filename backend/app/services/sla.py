"""SLA configuration (Module 10, phase 2): the organization's business calendar and its
policies. Admins change them; staff read them. Every change is audit-logged."""

import uuid
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import utcnow
from app.domain.business_time import (
    Calendar,
    CalendarError,
    calculate_deadline,
    next_working_instant,
    subtract_business_minutes,
)
from app.models import (
    BusinessCalendar,
    BusinessHoliday,
    BusinessHours,
    OrganizationMember,
    OrgRole,
    SlaPolicy,
    TicketPriority,
)
from app.schemas.sla import CalendarUpdate, HolidayCreate, PolicyCreate, PolicyUpdate
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role

P = TicketPriority
# Editable starting points (the feature plan's seed values), in business minutes:
# (first response, resolution, warning before each deadline).
DEFAULT_TARGETS: dict[TicketPriority, tuple[int, int, int]] = {
    P.URGENT: (60, 8 * 60, 30),
    P.HIGH: (2 * 60, 16 * 60, 60),
    P.NORMAL: (4 * 60, 24 * 60, 60),
    P.LOW: (8 * 60, 40 * 60, 60),
}
DEFAULT_HOURS = [(day, time(9), time(17)) for day in range(1, 6)]  # Mon-Fri 09:00-17:00
PRIORITY_ORDER = {P.URGENT: 0, P.HIGH: 1, P.NORMAL: 2, P.LOW: 3, None: 4}


def _label(priority: TicketPriority | None) -> str:
    return priority.value.capitalize() if priority else "Default"


def _hours_text(hours: list[tuple[int, time, time]]) -> str:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return ", ".join(
        f"{days[day - 1]} {start:%H:%M}-{'24:00' if end == time(0) else f'{end:%H:%M}'}"
        for day, start, end in sorted(hours)
    )


class SlaConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Reading ----------------------------------------------------------------------

    async def calendar(self, organization_id: uuid.UUID) -> BusinessCalendar | None:
        return await self.session.scalar(
            select(BusinessCalendar)
            .options(selectinload(BusinessCalendar.hours), selectinload(BusinessCalendar.holidays))
            .where(
                BusinessCalendar.organization_id == organization_id,
                BusinessCalendar.is_default.is_(True),
            )
        )

    async def policies(self, organization_id: uuid.UUID) -> list[SlaPolicy]:
        rows = (
            await self.session.scalars(
                select(SlaPolicy).where(SlaPolicy.organization_id == organization_id)
            )
        ).all()
        return sorted(rows, key=lambda p: (not p.is_active, PRIORITY_ORDER[p.priority], p.name))

    async def config(
        self, member: OrganizationMember
    ) -> tuple[BusinessCalendar | None, list[SlaPolicy]]:
        require_role(member, *STAFF_ROLES)
        return (
            await self.calendar(member.organization_id),
            await self.policies(member.organization_id),
        )

    async def policy_for(
        self, organization_id: uuid.UUID, priority: TicketPriority
    ) -> SlaPolicy | None:
        """The active policy for a priority, else the active default, else none."""
        active = [p for p in await self.policies(organization_id) if p.is_active]
        return next((p for p in active if p.priority == priority), None) or next(
            (p for p in active if p.priority is None), None
        )

    async def preview(
        self,
        member: OrganizationMember,
        priority: TicketPriority,
        opened_at: datetime | None = None,
    ) -> tuple[SlaPolicy | None, Calendar, dict[str, datetime | None]]:
        require_role(member, *STAFF_ROLES)
        calendar = await self._require_calendar(member.organization_id)
        cal = calendar.to_calendar()
        if opened_at is not None and opened_at.utcoffset() is None:
            raise AppError("Give the time with a time zone offset", code="naive_datetime")
        opened_at = opened_at or utcnow()
        policy = await self.policy_for(member.organization_id, priority)
        starts_at = next_working_instant(opened_at, cal)
        times: dict[str, datetime | None] = {
            "opened_at": opened_at,
            "clock_starts_at": starts_at,
            "first_response_due": None,
            "first_response_warning": None,
            "resolution_due": None,
            "resolution_warning": None,
        }
        if policy is not None:
            for kind, minutes in (
                ("first_response", policy.first_response_minutes),
                ("resolution", policy.resolution_minutes),
            ):
                due = calculate_deadline(opened_at, minutes, cal)
                warning = subtract_business_minutes(due, policy.warning_minutes, cal)
                times[f"{kind}_due"] = due
                times[f"{kind}_warning"] = max(warning, starts_at)
        return policy, cal, times

    # --- Setting up ---------------------------------------------------------------------

    async def set_up(self, member: OrganizationMember, timezone: str) -> None:
        """A Monday-Friday 09:00-17:00 calendar and one policy per priority, to edit."""
        require_role(member, OrgRole.ADMIN)
        if await self.calendar(member.organization_id) is not None:
            raise ConflictError("Service levels are already set up", code="sla_already_set_up")
        self._validate(timezone, DEFAULT_HOURS)
        calendar = BusinessCalendar(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            name="Business hours",
            timezone=timezone,
            is_default=True,
            hours=[
                BusinessHours(
                    organization_id=member.organization_id,
                    weekday=day,
                    start_time=start,
                    end_time=end,
                )
                for day, start, end in DEFAULT_HOURS
            ],
            holidays=[],
        )
        self.session.add(calendar)
        for priority, (first, resolution, warning) in DEFAULT_TARGETS.items():
            self.session.add(
                SlaPolicy(
                    organization_id=member.organization_id,
                    name=_label(priority),
                    priority=priority,
                    first_response_minutes=first,
                    resolution_minutes=resolution,
                    warning_minutes=warning,
                    is_active=True,
                    version=1,
                )
            )
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=calendar.id,
            action="sla.set_up",
            summary=f"Set up service levels ({timezone}, Mon-Fri 09:00-17:00, "
            "a policy per priority)",
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:  # set up concurrently
            await self.session.rollback()
            raise ConflictError(
                "Service levels are already set up", code="sla_already_set_up"
            ) from exc

    # --- Calendar ----------------------------------------------------------------------

    async def update_calendar(
        self, member: OrganizationMember, data: CalendarUpdate
    ) -> BusinessCalendar:
        require_role(member, OrgRole.ADMIN)
        calendar = await self._require_calendar(member.organization_id)
        new_hours = [(h.weekday, h.start, h.end) for h in data.hours]
        self._validate(data.timezone, new_hours)
        old_hours = [(h.weekday, h.start_time, h.end_time) for h in calendar.hours]
        changes = {}
        if data.timezone != calendar.timezone:
            changes["timezone"] = {"from": calendar.timezone, "to": data.timezone}
        if sorted(old_hours) != sorted(new_hours):
            changes["hours"] = {"from": _hours_text(old_hours), "to": _hours_text(new_hours)}
        if not changes:
            return calendar
        calendar.timezone = data.timezone
        calendar.hours = [
            BusinessHours(
                organization_id=member.organization_id,
                weekday=day,
                start_time=start,
                end_time=end,
            )
            for day, start, end in sorted(new_hours)
        ]
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=calendar.id,
            action="sla.calendar_changed",
            summary="Changed the business calendar: "
            + "; ".join(f"{key} {c['to']}" for key, c in changes.items()),
            changes=changes,
        )
        await self.session.commit()
        return await self._require_calendar(member.organization_id)

    async def add_holiday(self, member: OrganizationMember, data: HolidayCreate) -> BusinessHoliday:
        require_role(member, OrgRole.ADMIN)
        calendar = await self._require_calendar(member.organization_id)
        holiday = BusinessHoliday(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            calendar_id=calendar.id,
            date=data.date,
            name=data.name,
        )
        self.session.add(holiday)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=calendar.id,
            action="sla.holiday_added",
            summary=f"Added the holiday {data.name} on {data.date:%Y-%m-%d}",
        )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"{data.date:%Y-%m-%d} is already a holiday", code="holiday_exists"
            ) from exc
        return holiday

    async def remove_holiday(self, member: OrganizationMember, holiday_id: uuid.UUID) -> None:
        require_role(member, OrgRole.ADMIN)
        holiday = await self.session.get(BusinessHoliday, holiday_id)
        if holiday is None or holiday.organization_id != member.organization_id:
            raise NotFoundError("Holiday not found")
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=holiday.calendar_id,
            action="sla.holiday_removed",
            summary=f"Removed the holiday {holiday.name} on {holiday.date:%Y-%m-%d}",
        )
        await self.session.delete(holiday)
        await self.session.commit()

    # --- Policies ----------------------------------------------------------------------

    async def create_policy(self, member: OrganizationMember, data: PolicyCreate) -> SlaPolicy:
        require_role(member, OrgRole.ADMIN)
        await self._require_calendar(member.organization_id)
        policy = SlaPolicy(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            name=data.name or _label(data.priority),
            priority=data.priority,
            first_response_minutes=data.first_response_minutes,
            resolution_minutes=data.resolution_minutes,
            warning_minutes=data.warning_minutes,
            is_active=True,
            version=1,
        )
        self.session.add(policy)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=policy.id,
            action="sla.policy_created",
            summary=f"Added the SLA policy {policy.name}",
        )
        await self._commit_policy(_label(data.priority))
        return policy

    async def update_policy(
        self, member: OrganizationMember, policy_id: uuid.UUID, data: PolicyUpdate
    ) -> SlaPolicy:
        require_role(member, OrgRole.ADMIN)
        policy = await self.session.get(SlaPolicy, policy_id)
        if policy is None or policy.organization_id != member.organization_id:
            raise NotFoundError("SLA policy not found")
        changes = {
            field: {"from": getattr(policy, field), "to": value}
            for field, value in data.model_dump(exclude_none=True).items()
            if getattr(policy, field) != value
        }
        if not changes:
            return policy
        first = changes.get("first_response_minutes", {}).get("to", policy.first_response_minutes)
        resolution = changes.get("resolution_minutes", {}).get("to", policy.resolution_minutes)
        warning = changes.get("warning_minutes", {}).get("to", policy.warning_minutes)
        if resolution < first or warning >= first:
            raise AppError(
                "Resolution must not be shorter than the first response, and the warning must "
                "come before the first-response target",
                code="invalid_sla_targets",
            )
        for field, change in changes.items():
            setattr(policy, field, change["to"])
        if {"first_response_minutes", "resolution_minutes", "warning_minutes"} & set(changes):
            policy.version += 1
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="sla",
            entity_id=policy.id,
            action="sla.policy_changed",
            summary=f"Changed the SLA policy {policy.name}",
            changes=changes,
        )
        await self._commit_policy(_label(policy.priority))
        return policy

    # --- Helpers -----------------------------------------------------------------------

    async def _require_calendar(self, organization_id: uuid.UUID) -> BusinessCalendar:
        calendar = await self.calendar(organization_id)
        if calendar is None:
            raise NotFoundError("Service levels aren't set up yet", code="sla_not_set_up")
        return calendar

    def _validate(self, timezone: str, hours: list[tuple[int, time, time]]) -> Calendar:
        by_day: dict[int, list[tuple[time, time]]] = {}
        for day, start, end in hours:
            by_day.setdefault(day, []).append((start, end))
        try:
            return Calendar(timezone, {d: tuple(i) for d, i in by_day.items()})
        except CalendarError as exc:
            raise AppError(str(exc), code="invalid_calendar") from exc

    async def _commit_policy(self, label: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError(
                f"There is already an active {label.lower()} policy", code="sla_policy_exists"
            ) from exc
