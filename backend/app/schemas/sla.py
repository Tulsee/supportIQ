"""SLA configuration (Module 10, phase 2): business calendar and policies."""

import uuid
from datetime import date, datetime, time
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models import BusinessCalendar, BusinessHoliday, SlaPolicy, TicketPriority

# Up to a year of business minutes.
Minutes = Annotated[int, Field(ge=1, le=525_600)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TimeZone = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class HoursInterval(BaseModel):
    weekday: int = Field(ge=1, le=7, description="1 = Monday ... 7 = Sunday.")
    start: time
    end: time = Field(description="00:00 means midnight (the end of the day).")

    @field_validator("start", "end")
    @classmethod
    def _whole_minutes(cls, value: time) -> time:
        if value.second or value.microsecond or value.tzinfo:
            raise ValueError("Use whole minutes in the calendar's own time zone")
        return value


class HolidayCreate(BaseModel):
    date: date
    name: Name


class HolidayRead(BaseModel):
    id: uuid.UUID
    date: date
    name: str

    @classmethod
    def build(cls, holiday: BusinessHoliday) -> Self:
        return cls(id=holiday.id, date=holiday.date, name=holiday.name)


class CalendarUpdate(BaseModel):
    timezone: TimeZone = Field(description="An IANA time zone, e.g. Asia/Kathmandu.")
    hours: list[HoursInterval] = Field(max_length=28)


class CalendarRead(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    hours: list[HoursInterval]
    holidays: list[HolidayRead]

    @classmethod
    def build(cls, calendar: BusinessCalendar) -> Self:
        return cls(
            id=calendar.id,
            name=calendar.name,
            timezone=calendar.timezone,
            hours=[
                HoursInterval(weekday=h.weekday, start=h.start_time, end=h.end_time)
                for h in calendar.hours
            ],
            holidays=[HolidayRead.build(h) for h in calendar.holidays],
        )


class _Targets(BaseModel):
    @model_validator(mode="after")
    def _consistent(self) -> Self:
        first = getattr(self, "first_response_minutes", None)
        resolution = getattr(self, "resolution_minutes", None)
        warning = getattr(self, "warning_minutes", None)
        if first is not None and resolution is not None and resolution < first:
            raise ValueError("The resolution target can't be shorter than the first response")
        if first is not None and warning is not None and warning >= first:
            raise ValueError("The warning must come before the first-response target")
        return self


class PolicyCreate(_Targets):
    priority: TicketPriority | None = Field(
        description="The priority this policy covers; null for the default policy (any "
        "priority without its own)."
    )
    name: Name | None = Field(default=None, description="Defaults to the priority's name.")
    first_response_minutes: Minutes
    resolution_minutes: Minutes
    warning_minutes: Annotated[int, Field(ge=0, le=525_600)] = 60


class PolicyUpdate(_Targets):
    """Only the fields provided are changed. Changing a target raises the version;
    ticket clocks keep the targets they started with."""

    name: Name | None = None
    first_response_minutes: Minutes | None = None
    resolution_minutes: Minutes | None = None
    warning_minutes: Annotated[int, Field(ge=0, le=525_600)] | None = None
    is_active: bool | None = None


class PolicyRead(BaseModel):
    id: uuid.UUID
    name: str
    priority: TicketPriority | None
    first_response_minutes: int
    resolution_minutes: int
    warning_minutes: int
    is_active: bool
    version: int
    updated_at: datetime

    @classmethod
    def build(cls, policy: SlaPolicy) -> Self:
        return cls(
            id=policy.id,
            name=policy.name,
            priority=policy.priority,
            first_response_minutes=policy.first_response_minutes,
            resolution_minutes=policy.resolution_minutes,
            warning_minutes=policy.warning_minutes,
            is_active=policy.is_active,
            version=policy.version,
            updated_at=policy.updated_at,
        )


class SlaSetup(BaseModel):
    timezone: TimeZone = "Asia/Kathmandu"


class SlaConfig(BaseModel):
    """The organization's calendar (null until SLAs are set up) and its policies."""

    calendar: CalendarRead | None
    policies: list[PolicyRead]


class SlaPreview(BaseModel):
    """When a ticket of this priority opened at ``opened_at`` would be due."""

    priority: TicketPriority
    policy: PolicyRead | None = Field(description="Null: no policy covers this priority.")
    timezone: str
    opened_at: datetime
    clock_starts_at: datetime = Field(description="The next working instant.")
    first_response_due: datetime | None
    first_response_warning: datetime | None
    resolution_due: datetime | None
    resolution_warning: datetime | None
