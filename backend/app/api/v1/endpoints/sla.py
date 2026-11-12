"""Service levels for the organization in ``X-Organization-Id`` (Module 10, phase 2):
the business calendar and SLA policies. Staff read; admins change."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, TenantMembership
from app.models import TicketPriority
from app.schemas.common import error_responses
from app.schemas.sla import (
    CalendarRead,
    CalendarUpdate,
    HolidayCreate,
    HolidayRead,
    PolicyCreate,
    PolicyRead,
    PolicyUpdate,
    SlaConfig,
    SlaPreview,
    SlaSetup,
)
from app.services.sla import SlaConfigService

router = APIRouter(
    prefix="/sla", tags=["service levels"], responses=error_responses(400, 401, 403, 404, 409, 422)
)


async def _config(service: SlaConfigService, member: TenantMembership) -> SlaConfig:
    calendar, policies = await service.config(member)
    return SlaConfig(
        calendar=CalendarRead.build(calendar) if calendar else None,
        policies=[PolicyRead.build(p) for p in policies],
    )


@router.get("", summary="The business calendar and SLA policies")
async def get_config(member: TenantMembership, session: DbSession) -> SlaConfig:
    """`calendar` is null until an admin sets up service levels."""
    return await _config(SlaConfigService(session), member)


@router.post("/setup", status_code=status.HTTP_201_CREATED, summary="Set up service levels (admin)")
async def set_up(body: SlaSetup, member: TenantMembership, session: DbSession) -> SlaConfig:
    """A Monday-Friday 09:00-17:00 calendar in the given time zone and a policy per
    priority (urgent 1 h / 8 h, high 2 h / 16 h, normal 4 h / 24 h, low 8 h / 40 h of
    business time), to adjust. 409 `sla_already_set_up` if done before."""
    service = SlaConfigService(session)
    await service.set_up(member, body.timezone)
    return await _config(service, member)


@router.put("/calendar", summary="Change the time zone and working hours (admin)")
async def update_calendar(
    body: CalendarUpdate, member: TenantMembership, session: DbSession
) -> CalendarRead:
    """Replaces the weekly hours. 400 `invalid_calendar` for an unknown time zone,
    overlapping or empty hours."""
    return CalendarRead.build(await SlaConfigService(session).update_calendar(member, body))


@router.post(
    "/calendar/holidays", status_code=status.HTTP_201_CREATED, summary="Add a holiday (admin)"
)
async def add_holiday(
    body: HolidayCreate, member: TenantMembership, session: DbSession
) -> HolidayRead:
    """No working time on that date. 409 `holiday_exists` if it already is one."""
    return HolidayRead.build(await SlaConfigService(session).add_holiday(member, body))


@router.delete(
    "/calendar/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a holiday (admin)",
)
async def remove_holiday(
    holiday_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> Response:
    await SlaConfigService(session).remove_holiday(member, holiday_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/policies", status_code=status.HTTP_201_CREATED, summary="Add an SLA policy (admin)")
async def create_policy(
    body: PolicyCreate, member: TenantMembership, session: DbSession
) -> PolicyRead:
    """409 `sla_policy_exists` if the priority already has an active policy."""
    return PolicyRead.build(await SlaConfigService(session).create_policy(member, body))


@router.patch("/policies/{policy_id}", summary="Change an SLA policy (admin)")
async def update_policy(
    policy_id: uuid.UUID, body: PolicyUpdate, member: TenantMembership, session: DbSession
) -> PolicyRead:
    return PolicyRead.build(await SlaConfigService(session).update_policy(member, policy_id, body))


@router.get("/preview", summary="When would a ticket be due?")
async def preview(
    member: TenantMembership,
    session: DbSession,
    priority: TicketPriority = TicketPriority.NORMAL,
    opened_at: Annotated[
        datetime | None,
        Query(description="With a time zone offset; defaults to now."),
    ] = None,
) -> SlaPreview:
    """The deadlines (and warning times) a ticket of this priority opened at
    `opened_at` would get, in business time."""
    policy, calendar, times = await SlaConfigService(session).preview(member, priority, opened_at)
    return SlaPreview(
        priority=priority,
        policy=PolicyRead.build(policy) if policy else None,
        timezone=calendar.timezone,
        **times,
    )
