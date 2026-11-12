"""SLA configuration (Module 10, phase 2): setup with defaults, the business calendar,
holidays, policies with versions, the deadline preview, permissions and isolation."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_rls_context
from tests.factories import API, Workspace, create_org, create_user, in_org

pytestmark = pytest.mark.anyio

SLA = f"{API}/sla"
KTM = ZoneInfo("Asia/Kathmandu")
FRIDAY_1630 = "2026-11-13T16:30:00+05:45"


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(KTM).replace(tzinfo=None)


async def set_up(api: httpx2.AsyncClient, ws: Workspace) -> dict:
    response = await api.post(
        f"{SLA}/setup", json={"timezone": "Asia/Kathmandu"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def preview(api: httpx2.AsyncClient, ws: Workspace, priority: str) -> dict:
    response = await api.get(
        f"{SLA}/preview",
        params={"priority": priority, "opened_at": FRIDAY_1630},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_setup_creates_an_editable_calendar_and_policies(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    before = (await api.get(SLA, headers=in_org(ws.agent, ws.org_id))).json()
    assert before == {"calendar": None, "policies": []}
    not_yet = await api.get(f"{SLA}/preview", headers=in_org(ws.agent, ws.org_id))
    assert not_yet.status_code == 404
    assert not_yet.json()["error"]["code"] == "sla_not_set_up"

    config = await set_up(api, ws)

    calendar = config["calendar"]
    assert calendar["timezone"] == "Asia/Kathmandu"
    assert [(h["weekday"], h["start"], h["end"]) for h in calendar["hours"]] == [
        (day, "09:00:00", "17:00:00") for day in range(1, 6)
    ]
    targets = {
        p["priority"]: (p["first_response_minutes"], p["resolution_minutes"], p["warning_minutes"])
        for p in config["policies"]
    }
    assert targets == {
        "urgent": (60, 480, 30),
        "high": (120, 960, 60),
        "normal": (240, 1440, 60),
        "low": (480, 2400, 60),
    }
    again = await api.post(f"{SLA}/setup", json={}, headers=in_org(ws.admin, ws.org_id))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "sla_already_set_up"


async def test_preview_counts_business_time_in_the_organizations_zone(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    await set_up(api, ws)

    high = await preview(api, ws, "high")

    assert high["timezone"] == "Asia/Kathmandu"
    assert local(high["clock_starts_at"]) == datetime(2026, 11, 13, 16, 30)
    # 2 business hours from Friday 16:30: 30 min on Friday, 90 on Monday.
    assert local(high["first_response_due"]) == datetime(2026, 11, 16, 10, 30)
    assert local(high["first_response_warning"]) == datetime(2026, 11, 16, 9, 30)
    # 16 business hours: 30 min Friday, 8 h Monday, 7 h 30 Tuesday.
    assert local(high["resolution_due"]) == datetime(2026, 11, 17, 16, 30)

    naive = await api.get(
        f"{SLA}/preview",
        params={"opened_at": "2026-11-13T16:30:00"},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert naive.status_code == 400
    assert naive.json()["error"]["code"] == "naive_datetime"


async def test_holidays_and_hours_move_the_deadlines(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    await set_up(api, ws)
    admin = in_org(ws.admin, ws.org_id)

    holiday = await api.post(
        f"{SLA}/calendar/holidays", json={"date": "2026-11-16", "name": "Tihar"}, headers=admin
    )
    assert holiday.status_code == 201, holiday.text
    assert local((await preview(api, ws, "high"))["first_response_due"]) == datetime(
        2026, 11, 17, 10, 30
    )
    duplicate = await api.post(
        f"{SLA}/calendar/holidays", json={"date": "2026-11-16", "name": "Again"}, headers=admin
    )
    assert duplicate.status_code == 409
    removed = await api.delete(f"{SLA}/calendar/holidays/{holiday.json()['id']}", headers=admin)
    assert removed.status_code == 204

    # Sunday to Friday, with a lunch break.
    hours = [
        {"weekday": day, "start": start, "end": end}
        for day in (7, 1, 2, 3, 4, 5)
        for start, end in (("09:00", "13:00"), ("14:00", "17:00"))
    ]
    changed = await api.put(
        f"{SLA}/calendar", json={"timezone": "Asia/Kathmandu", "hours": hours}, headers=admin
    )
    assert changed.status_code == 200, changed.text
    assert len(changed.json()["hours"]) == 12
    # Friday 16:30 + 2 h: 30 min Friday, then Sunday 09:00-10:30.
    assert local((await preview(api, ws, "high"))["first_response_due"]) == datetime(
        2026, 11, 15, 10, 30
    )

    for body, expected in (
        ({"timezone": "Mars/Olympus_Mons", "hours": hours}, "time zone"),
        (
            {
                "timezone": "UTC",
                "hours": [
                    {"weekday": 1, "start": "09:00", "end": "12:00"},
                    {"weekday": 1, "start": "11:00", "end": "17:00"},
                ],
            },
            "overlap",
        ),
        ({"timezone": "UTC", "hours": []}, "no working hours"),
    ):
        bad = await api.put(f"{SLA}/calendar", json=body, headers=admin)
        assert bad.status_code == 400, body
        assert bad.json()["error"]["code"] == "invalid_calendar"
        assert expected in bad.json()["error"]["message"]

    log = (await api.get(f"{API}/audit-log", headers=admin)).json()["items"]
    assert [e["action"] for e in log[:4]] == [
        "sla.calendar_changed",
        "sla.holiday_removed",
        "sla.holiday_added",
        "sla.set_up",
    ]


async def test_policies_are_versioned_and_fall_back_to_the_default(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    config = await set_up(api, ws)
    admin = in_org(ws.admin, ws.org_id)
    urgent = next(p for p in config["policies"] if p["priority"] == "urgent")

    faster = await api.patch(
        f"{SLA}/policies/{urgent['id']}", json={"first_response_minutes": 45}, headers=admin
    )
    assert faster.status_code == 200, faster.text
    assert faster.json()["version"] == 2
    renamed = await api.patch(
        f"{SLA}/policies/{urgent['id']}", json={"name": "Outages"}, headers=admin
    )
    assert renamed.json()["version"] == 2  # names don't change targets

    for body in (
        {"warning_minutes": 45},  # not before the first-response target
        {"resolution_minutes": 30},  # shorter than the first response
    ):
        bad = await api.patch(f"{SLA}/policies/{urgent['id']}", json=body, headers=admin)
        assert bad.status_code == 400, body
        assert bad.json()["error"]["code"] == "invalid_sla_targets"
    invalid = await api.post(
        f"{SLA}/policies",
        json={"priority": None, "first_response_minutes": 60, "resolution_minutes": 30},
        headers=admin,
    )
    assert invalid.status_code == 422

    await api.patch(f"{SLA}/policies/{urgent['id']}", json={"is_active": False}, headers=admin)
    uncovered = await preview(api, ws, "urgent")
    assert uncovered["policy"] is None and uncovered["first_response_due"] is None

    default = await api.post(
        f"{SLA}/policies",
        json={
            "priority": None,
            "first_response_minutes": 30,
            "resolution_minutes": 240,
            "warning_minutes": 10,
        },
        headers=admin,
    )
    assert default.status_code == 201, default.text
    assert default.json()["name"] == "Default"
    covered = await preview(api, ws, "urgent")
    assert covered["policy"]["id"] == default.json()["id"]
    assert local(covered["first_response_due"]) == datetime(2026, 11, 13, 17, 0)

    second_high = await api.post(
        f"{SLA}/policies",
        json={
            "priority": "high",
            "first_response_minutes": 60,
            "resolution_minutes": 120,
            "warning_minutes": 30,
        },
        headers=admin,
    )
    assert second_high.status_code == 409
    assert second_high.json()["error"]["code"] == "sla_policy_exists"


async def test_only_admins_change_service_levels(api: httpx2.AsyncClient, ws: Workspace) -> None:
    await set_up(api, ws)
    agent = in_org(ws.agent, ws.org_id)
    assert (await api.get(SLA, headers=agent)).status_code == 200
    assert (await api.get(SLA, headers=in_org(ws.customer, ws.org_id))).status_code == 403
    for method, url, body in (
        ("PUT", f"{SLA}/calendar", {"timezone": "UTC", "hours": []}),
        ("POST", f"{SLA}/calendar/holidays", {"date": "2026-12-25", "name": "X"}),
        ("POST", f"{SLA}/setup", {}),
    ):
        response = await api.request(method, url, json=body, headers=agent)
        assert response.status_code == 403, url

    outsider = await create_user(api)
    other = await create_org(api, outsider)
    theirs = (await api.get(SLA, headers=in_org(outsider, other["id"]))).json()
    assert theirs == {"calendar": None, "policies": []}


async def test_customers_cannot_read_sla_tables(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    await set_up(api, ws)
    org = uuid.UUID(ws.org_id)
    for table in ("business_calendars", "business_hours", "sla_policies"):
        await set_rls_context(
            db_session, organization_id=org, user_id=uuid.UUID(ws.customer.id), bypass=False
        )
        count = (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
        assert count == 0, table
