"""SLA configuration (Module 10, phase 2): a business calendar per organization and
first-response / resolution targets per ticket priority. Ticket clocks come in
phase 3."""

import enum
import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.business_time import Calendar
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.ticket import TicketPriority


class BusinessHours(UUIDPrimaryKeyMixin, Base):
    """One working interval on one weekday (1 = Monday) in the calendar's time zone.
    An interval ending at 00:00 runs to midnight."""

    __tablename__ = "business_hours"
    __table_args__ = (CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_calendars.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)


class BusinessHoliday(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "business_holidays"
    __table_args__ = (UniqueConstraint("calendar_id", "date"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_calendars.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(100))


class BusinessCalendar(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """When the organization works. One default calendar per organization for now;
    the schema allows more (e.g. per team) later."""

    __tablename__ = "business_calendars"
    __table_args__ = (
        Index(
            "uq_business_calendars_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64))  # IANA, e.g. Asia/Kathmandu
    is_default: Mapped[bool] = mapped_column(default=True, server_default=text("true"))

    hours: Mapped[list[BusinessHours]] = relationship(
        lazy="raise",
        cascade="all, delete-orphan",
        order_by=(BusinessHours.weekday, BusinessHours.start_time),
    )
    holidays: Mapped[list[BusinessHoliday]] = relationship(
        lazy="raise", cascade="all, delete-orphan", order_by=BusinessHoliday.date
    )

    def to_calendar(self) -> Calendar:
        """The pure calendar used for deadline arithmetic (hours and holidays loaded)."""
        by_day: dict[int, list[tuple[time, time]]] = {}
        for interval in self.hours:
            by_day.setdefault(interval.weekday, []).append((interval.start_time, interval.end_time))
        return Calendar(
            self.timezone,
            {day: tuple(intervals) for day, intervals in by_day.items()},
            frozenset(h.date for h in self.holidays),
        )


class SlaPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Targets in business minutes for one priority (or, with ``priority`` null, the
    organization's default for priorities without their own). ``version`` goes up
    whenever a target changes; ticket clocks record the version they used."""

    __tablename__ = "sla_policies"
    __table_args__ = (
        Index(
            "uq_sla_policies_active_priority",
            "organization_id",
            "priority",
            unique=True,
            postgresql_where=text("is_active AND priority IS NOT NULL"),
        ),
        Index(
            "uq_sla_policies_active_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_active AND priority IS NULL"),
        ),
        CheckConstraint(
            "first_response_minutes > 0 AND resolution_minutes > 0 AND warning_minutes >= 0",
            name="targets",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[TicketPriority | None] = mapped_column(
        str_enum_type(TicketPriority, "ticket_priority")
    )
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)
    warning_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class SlaClockKind(enum.StrEnum):
    FIRST_RESPONSE = "first_response"
    RESOLUTION = "resolution"


class SlaClockState(enum.StrEnum):
    """Stored state. ``at_risk`` and ``breached`` are also derived on read from the
    deadline, so a ticket shows them before the monitor (phase 4) records them."""

    RUNNING = "running"
    PAUSED = "paused"
    MET = "met"
    CANCELLED = "cancelled"  # closed without resolving, or no policy for a new priority


class TicketSlaClock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One SLA target on one ticket. The deadline is ``started_at`` plus
    ``target_minutes + paused_minutes`` of business time in ``calendar_snapshot`` (the
    calendar as it was when the clock started). A reopened ticket gets a new
    resolution cycle; earlier cycles keep their outcome."""

    __tablename__ = "ticket_sla_clocks"
    __table_args__ = (
        UniqueConstraint("ticket_id", "kind", "cycle"),
        Index(
            "ix_ticket_sla_clocks_organization_id_state_due_at",
            "organization_id",
            "state",
            "due_at",
        ),
        Index("ix_ticket_sla_clocks_state_warn_at", "state", "warn_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[SlaClockKind] = mapped_column(str_enum_type(SlaClockKind, "sla_clock_kind"))
    cycle: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sla_policies.id", ondelete="SET NULL")
    )
    policy_name: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[int] = mapped_column(Integer)
    target_minutes: Mapped[int] = mapped_column(Integer)
    warning_minutes: Mapped[int] = mapped_column(Integer)
    calendar_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[SlaClockState] = mapped_column(str_enum_type(SlaClockState, "sla_clock_state"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paused_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    paused_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    at_risk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TicketSlaPause(UUIDPrimaryKeyMixin, Base):
    """A period a clock didn't run (for now: the ticket waited for the customer)."""

    __tablename__ = "ticket_sla_pauses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    clock_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ticket_sla_clocks.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
