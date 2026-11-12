"""Business time: deadlines and durations that count only working minutes.

A ``Calendar`` is an IANA time zone, working intervals per ISO weekday (1 = Monday,
7 = Sunday) in local time, and holiday dates. Every function takes timezone-aware
datetimes and returns instants in UTC.

Each working interval is turned into real instants (local time converted to UTC), and
the arithmetic is done on those, so a daylight-saving change inside an interval
counts the time that actually elapsed. An interval ending at 00:00 runs to midnight,
so ``(00:00, 00:00)`` is a whole day (round-the-clock support).

Never ``created_at + timedelta(hours=...)``: that ignores nights, weekends, holidays
and daylight saving.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# A calendar without any working time in this many days can't produce a deadline.
SEARCH_DAYS = 3 * 366
MIDNIGHT = time(0, 0)

Interval = tuple[time, time]


class CalendarError(ValueError):
    """The calendar can't be used: unknown time zone, bad or overlapping hours, or no
    working time at all."""


@dataclass(frozen=True, eq=False)
class Calendar:
    timezone: str
    hours: Mapping[int, tuple[Interval, ...]]
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        try:
            zone = ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarError(f"Unknown time zone {self.timezone!r}") from exc
        object.__setattr__(self, "_zone", zone)
        for weekday, intervals in self.hours.items():
            if weekday not in range(1, 8):
                raise CalendarError(f"Weekday {weekday} is not 1 (Monday) to 7 (Sunday)")
            ordered = sorted(intervals)
            for start, end in ordered:
                if end != MIDNIGHT and not start < end:
                    raise CalendarError("Each working interval must end after it starts")
            for (_, end), (next_start, _) in pairwise(ordered):
                if end == MIDNIGHT or next_start < end:
                    raise CalendarError("Working intervals on the same day overlap")
        if not any(self.hours.values()):
            raise CalendarError("The calendar has no working hours")

    @property
    def zone(self) -> ZoneInfo:
        return self._zone  # type: ignore[attr-defined, no-any-return]

    def intervals_on(self, day: date) -> list[tuple[datetime, datetime]]:
        """The working intervals of a local date, as UTC instants (none on holidays)."""
        if day in self.holidays:
            return []
        result = []
        for start, end in sorted(self.hours.get(day.isoweekday(), ())):
            begins = datetime.combine(day, start, tzinfo=self.zone).astimezone(UTC)
            end_day = day + timedelta(days=1) if end == MIDNIGHT else day
            ends = datetime.combine(end_day, end, tzinfo=self.zone).astimezone(UTC)
            if ends > begins:
                result.append((begins, ends))
        return result


def _require_aware(*instants: datetime) -> None:
    for instant in instants:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Business-time calculations need timezone-aware datetimes")


def _forward(calendar: Calendar, at: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Working time from ``at`` onwards, as (start, end) UTC pairs, in order."""
    first_day = at.astimezone(calendar.zone).date() - timedelta(days=1)
    for offset in range(SEARCH_DAYS):
        for begins, ends in calendar.intervals_on(first_day + timedelta(days=offset)):
            if ends > at:
                yield max(begins, at), ends
    raise CalendarError(f"No working time within {SEARCH_DAYS} days")


def _backward(calendar: Calendar, at: datetime) -> Iterator[tuple[datetime, datetime]]:
    """Working time before ``at``, latest first."""
    last_day = at.astimezone(calendar.zone).date()
    for offset in range(SEARCH_DAYS):
        for begins, ends in reversed(calendar.intervals_on(last_day - timedelta(days=offset))):
            if begins < at:
                yield begins, min(ends, at)
    raise CalendarError(f"No working time within {SEARCH_DAYS} days")


def calculate_deadline(start: datetime, minutes: int, calendar: Calendar) -> datetime:
    """The instant ``minutes`` of working time after ``start``. Starting outside working
    hours counts from the next opening; ``minutes=0`` gives that next working instant."""
    _require_aware(start)
    if minutes < 0:
        raise ValueError("A business-time target can't be negative")
    remaining = timedelta(minutes=minutes)
    for begins, ends in _forward(calendar, start.astimezone(UTC)):
        if remaining <= ends - begins:
            return begins + remaining
        remaining -= ends - begins
    raise AssertionError("unreachable")  # pragma: no cover


def subtract_business_minutes(end: datetime, minutes: int, calendar: Calendar) -> datetime:
    """The instant ``minutes`` of working time before ``end`` (e.g. a warning time)."""
    _require_aware(end)
    if minutes < 0:
        raise ValueError("A business-time amount can't be negative")
    if minutes == 0:
        return end.astimezone(UTC)
    remaining = timedelta(minutes=minutes)
    for begins, ends in _backward(calendar, end.astimezone(UTC)):
        if remaining <= ends - begins:
            return ends - remaining
        remaining -= ends - begins
    raise AssertionError("unreachable")  # pragma: no cover


def business_minutes_between(start: datetime, end: datetime, calendar: Calendar) -> int:
    """Whole working minutes from ``start`` to ``end`` (0 if ``end`` isn't later)."""
    _require_aware(start, end)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        return 0
    total = timedelta()
    for begins, ends in _forward(calendar, start):
        if begins >= end:
            break
        total += min(ends, end) - begins
    return int(total.total_seconds() // 60)


def next_working_instant(at: datetime, calendar: Calendar) -> datetime:
    """``at`` itself during working time, otherwise the next opening."""
    return calculate_deadline(at, 0, calendar)
