"""The business-time calculator (SLA deadlines), with fixed instants: Kathmandu's
UTC+05:45 offset, a daylight-saving zone (London), weekends, holidays, lunch breaks,
interval edges, round-the-clock calendars and invalid input."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.domain.business_time import (
    Calendar,
    CalendarError,
    business_minutes_between,
    calculate_deadline,
    next_working_instant,
    subtract_business_minutes,
)

KTM = ZoneInfo("Asia/Kathmandu")
LONDON = ZoneInfo("Europe/London")
NINE_TO_FIVE = ((time(9), time(17)),)
WEEKDAYS = dict.fromkeys(range(1, 6), NINE_TO_FIVE)  # Monday to Friday


def kathmandu(**extra) -> Calendar:
    return Calendar("Asia/Kathmandu", WEEKDAYS, **extra)


def at(zone: ZoneInfo, *parts: int) -> datetime:
    return datetime(*parts, tzinfo=zone)


# 2026-11-13 is a Friday; 2026-11-16 a Monday.


def test_a_deadline_inside_the_working_day() -> None:
    due = calculate_deadline(at(KTM, 2026, 11, 12, 10, 0), 120, kathmandu())
    assert due == at(KTM, 2026, 11, 12, 12, 0)
    assert due.tzinfo == UTC
    assert due == datetime(2026, 11, 12, 6, 15, tzinfo=UTC)  # 12:00 at UTC+05:45


def test_a_deadline_carries_over_the_weekend() -> None:
    # Friday 16:30 + 60 working minutes: 30 on Friday, 30 on Monday.
    due = calculate_deadline(at(KTM, 2026, 11, 13, 16, 30), 60, kathmandu())
    assert due == at(KTM, 2026, 11, 16, 9, 30)


def test_holidays_are_skipped() -> None:
    calendar = kathmandu(holidays=frozenset({date(2026, 11, 16)}))
    due = calculate_deadline(at(KTM, 2026, 11, 13, 16, 30), 60, calendar)
    assert due == at(KTM, 2026, 11, 17, 9, 30)


def test_starting_outside_working_hours_counts_from_the_next_opening() -> None:
    calendar = kathmandu()
    assert calculate_deadline(at(KTM, 2026, 11, 12, 7, 0), 30, calendar) == at(
        KTM, 2026, 11, 12, 9, 30
    )
    assert calculate_deadline(at(KTM, 2026, 11, 12, 19, 0), 30, calendar) == at(
        KTM, 2026, 11, 13, 9, 30
    )
    assert calculate_deadline(at(KTM, 2026, 11, 14, 11, 0), 30, calendar) == at(
        KTM, 2026, 11, 16, 9, 30
    )


def test_interval_edges() -> None:
    calendar = kathmandu()
    # Exactly at opening, zero minutes: now.
    assert calculate_deadline(at(KTM, 2026, 11, 12, 9, 0), 0, calendar) == at(
        KTM, 2026, 11, 12, 9, 0
    )
    # A target ending exactly at closing time is due at closing, not the next morning.
    assert calculate_deadline(at(KTM, 2026, 11, 12, 16, 0), 60, calendar) == at(
        KTM, 2026, 11, 12, 17, 0
    )
    # Starting exactly at closing time: the next morning.
    assert calculate_deadline(at(KTM, 2026, 11, 12, 17, 0), 1, calendar) == at(
        KTM, 2026, 11, 13, 9, 1
    )
    assert next_working_instant(at(KTM, 2026, 11, 13, 17, 0), calendar) == at(
        KTM, 2026, 11, 16, 9, 0
    )


def test_several_intervals_a_day() -> None:
    lunch_break = Calendar(
        "Asia/Kathmandu", dict.fromkeys(range(1, 6), ((time(9), time(12)), (time(13), time(17))))
    )
    assert calculate_deadline(at(KTM, 2026, 11, 12, 11, 30), 60, lunch_break) == at(
        KTM, 2026, 11, 12, 13, 30
    )
    assert (
        business_minutes_between(at(KTM, 2026, 11, 12, 9), at(KTM, 2026, 11, 12, 17), lunch_break)
        == 7 * 60
    )


def test_multi_day_targets() -> None:
    # 24 business hours = three 8-hour days.
    due = calculate_deadline(at(KTM, 2026, 11, 12, 10, 0), 24 * 60, kathmandu())
    assert due == at(KTM, 2026, 11, 17, 10, 0)  # Thu 7h, Fri 8h, Mon 8h, Tue 1h


def test_daylight_saving_in_business_hours() -> None:
    # London moves to BST on Sunday 2026-03-29. Friday 16:00 GMT + 2 working hours.
    calendar = Calendar("Europe/London", WEEKDAYS)
    due = calculate_deadline(at(LONDON, 2026, 3, 27, 16, 0), 120, calendar)
    assert due == at(LONDON, 2026, 3, 30, 10, 0)  # Monday 10:00 local
    assert due == datetime(2026, 3, 30, 9, 0, tzinfo=UTC)  # BST is UTC+1


def test_round_the_clock_counts_real_time_across_daylight_saving() -> None:
    always = Calendar("Europe/London", dict.fromkeys(range(1, 8), ((time(0), time(0)),)))
    # Saturday noon + 24 hours. Sunday has only 23 hours, so 24 real hours later is
    # Sunday 13:00 local time.
    due = calculate_deadline(at(LONDON, 2026, 3, 28, 12, 0), 24 * 60, always)
    assert due == datetime(2026, 3, 29, 12, 0, tzinfo=UTC)
    assert due.astimezone(LONDON).hour == 13
    # And back in October: Sunday has 25 hours.
    due = calculate_deadline(at(LONDON, 2026, 10, 24, 12, 0), 24 * 60, always)
    assert due.astimezone(LONDON) == at(LONDON, 2026, 10, 25, 11, 0)


def test_minutes_between_and_back() -> None:
    calendar = kathmandu()
    friday, monday = at(KTM, 2026, 11, 13, 16, 0), at(KTM, 2026, 11, 16, 10, 0)
    assert business_minutes_between(friday, monday, calendar) == 120
    assert business_minutes_between(monday, friday, calendar) == 0
    # Warning times walk backwards over the same calendar.
    assert subtract_business_minutes(monday, 60, calendar) == at(KTM, 2026, 11, 16, 9, 0)
    assert subtract_business_minutes(monday, 90, calendar) == at(KTM, 2026, 11, 13, 16, 30)
    assert subtract_business_minutes(monday, 0, calendar) == monday


def test_a_deadline_and_its_duration_agree() -> None:
    calendar = kathmandu(holidays=frozenset({date(2026, 11, 18)}))
    start = at(KTM, 2026, 11, 13, 14, 47)
    for minutes in (1, 59, 60, 133, 480, 481, 2400):
        due = calculate_deadline(start, minutes, calendar)
        assert business_minutes_between(start, due, calendar) == minutes
        assert subtract_business_minutes(due, minutes, calendar) == start.astimezone(UTC)


def test_invalid_input_is_rejected() -> None:
    calendar = kathmandu()
    with pytest.raises(ValueError, match="negative"):
        calculate_deadline(at(KTM, 2026, 11, 12, 10), -1, calendar)
    with pytest.raises(ValueError, match="timezone-aware"):
        calculate_deadline(datetime(2026, 11, 12, 10), 60, calendar)
    with pytest.raises(CalendarError, match="time zone"):
        Calendar("Mars/Olympus_Mons", WEEKDAYS)
    with pytest.raises(CalendarError, match="overlap"):
        Calendar("UTC", {1: ((time(9), time(12)), (time(11), time(17)))})
    with pytest.raises(CalendarError, match="end after"):
        Calendar("UTC", {1: ((time(17), time(9)),)})
    with pytest.raises(CalendarError, match="no working hours"):
        Calendar("UTC", {})
    with pytest.raises(CalendarError, match="Weekday"):
        Calendar("UTC", {8: NINE_TO_FIVE})
