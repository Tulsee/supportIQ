"""Service levels in depth (Module 10 follow-ups): compliance by priority, team and
agent, business-time medians, due-soon and breached per day, pause reasons, and a
reason recorded with a reassignment. Kathmandu, Monday-Friday 09:00-17:00; the
clock is moved by hand."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sla_monitor import evaluate_sla_clocks
from tests.factories import API, Workspace, create_ticket, in_org
from tests.test_teams import add_team

pytestmark = pytest.mark.anyio

KTM = ZoneInfo("Asia/Kathmandu")


def at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=KTM).astimezone(UTC)


class Clock:
    def __init__(self) -> None:
        self.now = at(2026, 11, 16, 9, 0)  # Monday 09:00

    def set(self, *parts: int) -> None:
        self.now = at(*parts)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr("app.services.sla_clocks.utcnow", lambda: clock.now)
    # The report's window: the week of Sunday 15 November.
    monkeypatch.setattr(
        "app.services.analytics._window",
        lambda days: {"since": datetime(2026, 11, 15, tzinfo=UTC)},
    )
    return clock


@pytest.fixture
async def sla(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await api.post(
        f"{API}/sla/setup", json={"timezone": "Asia/Kathmandu"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text


async def test_service_levels_in_depth(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, clock: Clock, db_session: AsyncSession
) -> None:
    staff = in_org(ws.agent, ws.org_id)
    customer = in_org(ws.customer, ws.org_id)
    team = await add_team(api, ws, "Support", [{"user_id": ws.agent.id}])
    normal = await create_ticket(api, ws.customer, ws.org_id, subject="Normal")
    customer_id = normal["customer"]["id"]
    urgent = await create_ticket(
        api, ws.agent, ws.org_id, subject="Urgent", customer_id=customer_id, priority="urgent"
    )
    await create_ticket(  # nobody answers this one
        api, ws.agent, ws.org_id, subject="Low", customer_id=customer_id, priority="low"
    )
    await api.patch(
        f"{API}/tickets/{urgent['id']}",
        json={"assignee_id": ws.agent.id, "team_id": team["id"]},
        headers=staff,
    )

    async def reply(ticket: dict, headers: dict) -> None:
        response = await api.post(
            f"{API}/tickets/{ticket['id']}/messages", json={"body": "Hello"}, headers=headers
        )
        assert response.status_code == 201, response.text

    clock.set(2026, 11, 16, 9, 30)
    await reply(urgent, staff)  # 30 business minutes, due 10:00: on time
    clock.set(2026, 11, 16, 11, 0)
    await reply(normal, staff)  # 120 business minutes, due 13:00: on time
    # Waiting for the customer for an hour pauses the resolution clock.
    await api.patch(
        f"{API}/tickets/{normal['id']}",
        json={"assignee_id": ws.agent.id, "status": "in_progress"},
        headers=staff,
    )
    clock.set(2026, 11, 16, 11, 30)
    await api.patch(
        f"{API}/tickets/{normal['id']}", json={"status": "waiting_for_customer"}, headers=staff
    )
    clock.set(2026, 11, 16, 12, 30)
    await reply(normal, customer)
    # Nobody answers the low-priority ticket (due Monday 17:00, eight business hours).
    await evaluate_sla_clocks(db_session, now=at(2026, 11, 17, 10, 0))

    response = await api.get(f"{API}/analytics/sla", params={"days": 7}, headers=staff)
    assert response.status_code == 200, response.text
    data = response.json()

    def rates(groups: list[dict]) -> dict[str, tuple]:
        return {
            g["label"]: (
                g["first_response"]["on_time"],
                g["first_response"]["overdue"],
                g["first_response"]["rate"],
            )
            for g in groups
        }

    assert rates(data["by_priority"]) == {
        "urgent": (1, 0, 1.0),
        "normal": (1, 0, 1.0),
        "low": (0, 1, 0.0),
    }
    assert [g["label"] for g in data["by_priority"]] == ["urgent", "normal", "low"]
    assert rates(data["by_team"]) == {"Support": (1, 0, 1.0), "No team": (1, 1, 0.5)}
    assert rates(data["by_agent"]) == {"Abe Agent": (2, 0, 1.0), "Unassigned": (0, 1, 0.0)}
    assert data["median_business_minutes"]["first_response"] == 75  # 30 and 120
    assert data["met"]["first_response"] == 2

    days = {d["day"]: d for d in data["daily"] if d["at_risk"] or d["breached"]}
    # Monday: the low ticket's first response and the urgent ticket's resolution (both
    # eight business hours) were flagged at 16:00 and breached at 17:00.
    assert days == {"2026-11-16": {"day": "2026-11-16", "at_risk": 2, "breached": 2}}
    assert data["pauses"] == [
        {"reason": "waiting_for_customer", "count": 1, "ongoing": 0, "median_minutes": 60.0}
    ]
    assert (
        await api.get(f"{API}/analytics/sla", headers=in_org(ws.customer, ws.org_id))
    ).status_code == 403


async def test_a_reassignment_can_say_why(api: httpx2.AsyncClient, ws: Workspace) -> None:
    staff = in_org(ws.agent, ws.org_id)
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"assignee_id": ws.agent.id}, headers=staff
    )
    moved = await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": ws.admin.id, "assignment_reason": "  Needs a refund approval  "},
        headers=staff,
    )
    assert moved.status_code == 200, moved.text
    released = await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": None, "assignment_reason": "Back to the queue"},
        headers=staff,
    )
    assert released.status_code == 200, released.text
    events = (await api.get(f"{API}/tickets/{ticket['id']}/events", headers=staff)).json()
    reasons = [(e["type"], e["data"].get("reason")) for e in events if "assigned" in e["type"]]
    assert reasons == [
        ("assigned", None),
        ("assigned", "Needs a refund approval"),
        ("unassigned", "Back to the queue"),
    ]
    too_long = await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": ws.agent.id, "assignment_reason": "x" * 301},
        headers=staff,
    )
    assert too_long.status_code == 422
