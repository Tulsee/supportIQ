"""SLA clocks on tickets (Module 10, phase 3), with a controlled clock: Kathmandu,
the default Monday-Friday 09:00-17:00 calendar, starting Monday 2026-11-16 09:00."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass, set_rls_context
from tests.factories import API, Workspace, create_customer, create_ticket, in_org

pytestmark = pytest.mark.anyio

KTM = ZoneInfo("Asia/Kathmandu")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def set(self, *parts: int) -> None:
        self.now = datetime(*parts, tzinfo=KTM).astimezone(UTC)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock(datetime(2026, 11, 16, 9, 0, tzinfo=KTM).astimezone(UTC))
    monkeypatch.setattr("app.services.sla_clocks.utcnow", fake)
    monkeypatch.setattr("app.api.v1.endpoints.tickets.utcnow", fake)
    return fake


@pytest.fixture
async def sla(api: httpx2.AsyncClient, ws: Workspace) -> dict:
    response = await api.post(
        f"{API}/sla/setup", json={"timezone": "Asia/Kathmandu"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


def local(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso).astimezone(KTM).replace(tzinfo=None) if iso else None


async def clocks(api: httpx2.AsyncClient, ws: Workspace, ticket: dict) -> dict:
    response = await api.get(
        f"{API}/tickets/{ticket['id']}/sla", headers=in_org(ws.agent, ws.org_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


def current(data: dict, kind: str) -> dict:
    return next(c for c in data["clocks"] if c["kind"] == kind)


async def act(api: httpx2.AsyncClient, ws: Workspace, ticket: dict, actor=None, **body) -> None:
    response = await api.patch(
        f"{API}/tickets/{ticket['id']}", json=body, headers=in_org(actor or ws.agent, ws.org_id)
    )
    assert response.status_code == 200, response.text


async def say(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, actor, body="Hi", **extra
) -> None:
    response = await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": body, **extra},
        headers=in_org(actor, ws.org_id),
    )
    assert response.status_code == 201, response.text


async def test_no_clocks_until_service_levels_are_set_up(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    assert await clocks(api, ws, ticket) == {"configured": False, "clocks": []}


async def test_a_new_ticket_gets_both_clocks_in_business_time(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    data = await clocks(api, ws, ticket)
    assert data["configured"] is True
    first, resolution = current(data, "first_response"), current(data, "resolution")
    assert (first["policy_name"], first["policy_version"], first["target_minutes"]) == (
        "Normal",
        1,
        240,
    )
    assert local(first["due_at"]) == datetime(2026, 11, 16, 13, 0)
    assert local(first["warn_at"]) == datetime(2026, 11, 16, 12, 0)
    assert (first["status"], first["remaining_minutes"]) == ("on_track", 240)
    # 24 business hours: three 8-hour days.
    assert local(resolution["due_at"]) == datetime(2026, 11, 18, 17, 0)
    assert resolution["timezone"] == "Asia/Kathmandu"

    clock.set(2026, 11, 16, 12, 30)
    first = current(await clocks(api, ws, ticket), "first_response")
    assert (first["status"], first["remaining_minutes"]) == ("at_risk", 30)

    clock.set(2026, 11, 16, 13, 30)
    first = current(await clocks(api, ws, ticket), "first_response")
    assert (first["status"], first["remaining_minutes"]) == ("breached", -30)

    # A late reply meets it, and the breach stays on record.
    await say(api, ws, ticket, ws.agent, "Sorry for the wait")
    first = current(await clocks(api, ws, ticket), "first_response")
    assert first["status"] == "met_late"
    assert local(first["breached_at"]) == datetime(2026, 11, 16, 13, 0)
    assert first["remaining_minutes"] is None


async def test_only_a_public_staff_reply_is_a_first_response(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict
) -> None:
    customer = await create_customer(api, ws.agent, ws.org_id, name="Phone Caller")
    # Logged by staff: the opening message is theirs, but nobody has answered yet.
    ticket = await create_ticket(api, ws.agent, ws.org_id, customer_id=customer["id"])
    await say(api, ws, ticket, ws.agent, "Checking the logs", is_internal=True)
    assert current(await clocks(api, ws, ticket), "first_response")["met_at"] is None

    clock.set(2026, 11, 16, 10, 15)
    await say(api, ws, ticket, ws.agent, "We're on it")
    first = current(await clocks(api, ws, ticket), "first_response")
    assert first["status"] == "met"
    assert local(first["met_at"]) == datetime(2026, 11, 16, 10, 15)


async def test_waiting_pauses_resolution_and_reopening_starts_a_new_cycle(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await act(api, ws, ticket, assignee_id=ws.agent.id, status="in_progress")

    clock.set(2026, 11, 16, 10, 0)
    await act(api, ws, ticket, status="waiting_for_customer")
    resolution = current(await clocks(api, ws, ticket), "resolution")
    assert (resolution["status"], resolution["remaining_minutes"]) == ("paused", 1380)

    # The customer answers the next morning: 8 business hours were paused.
    clock.set(2026, 11, 17, 10, 0)
    await say(api, ws, ticket, ws.customer, "Here are the details")
    resolution = current(await clocks(api, ws, ticket), "resolution")
    assert resolution["status"] == "on_track"
    assert resolution["paused_minutes"] == 480
    assert local(resolution["due_at"]) == datetime(2026, 11, 19, 17, 0)  # a day later
    assert resolution["remaining_minutes"] == 1380
    async with rls_bypass(db_session):
        pauses = (
            await db_session.execute(
                text(
                    "SELECT reason, ended_at IS NOT NULL FROM ticket_sla_pauses p "
                    "JOIN ticket_sla_clocks c ON c.id = p.clock_id WHERE c.ticket_id = :t"
                ),
                {"t": uuid.UUID(ticket["id"])},
            )
        ).all()
    assert pauses == [("waiting_for_customer", True)]

    clock.set(2026, 11, 17, 11, 0)
    await act(api, ws, ticket, status="resolved")
    assert current(await clocks(api, ws, ticket), "resolution")["status"] == "met"

    # The customer replies to the resolved ticket: it reopens, with a new cycle.
    clock.set(2026, 11, 17, 12, 0)
    await say(api, ws, ticket, ws.customer, "It broke again")
    data = await clocks(api, ws, ticket)
    resolutions = [c for c in data["clocks"] if c["kind"] == "resolution"]
    assert [(c["cycle"], c["status"]) for c in resolutions] == [(2, "on_track"), (1, "met")]
    assert local(resolutions[0]["started_at"]) == datetime(2026, 11, 17, 12, 0)
    assert local(resolutions[0]["due_at"]) == datetime(2026, 11, 20, 12, 0)


async def test_priority_changes_retarget_and_closing_cancels(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    clock.set(2026, 11, 16, 9, 10)
    await act(api, ws, ticket, priority="urgent")

    data = await clocks(api, ws, ticket)
    first, resolution = current(data, "first_response"), current(data, "resolution")
    assert (first["policy_name"], first["target_minutes"]) == ("Urgent", 60)
    assert local(first["due_at"]) == datetime(2026, 11, 16, 10, 0)  # from the original start
    assert local(resolution["due_at"]) == datetime(2026, 11, 16, 17, 0)

    close = await api.post(
        f"{API}/tickets/{ticket['id']}/close", headers=in_org(ws.agent, ws.org_id)
    )
    assert close.status_code == 200, close.text
    statuses = [c["status"] for c in (await clocks(api, ws, ticket))["clocks"]]
    assert statuses == ["cancelled", "cancelled"]


async def test_priorities_without_a_policy_have_no_clocks(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict
) -> None:
    low = next(p for p in sla["policies"] if p["priority"] == "low")
    await api.patch(
        f"{API}/sla/policies/{low['id']}",
        json={"is_active": False},
        headers=in_org(ws.admin, ws.org_id),
    )
    customer = await create_customer(api, ws.agent, ws.org_id)
    ticket = await create_ticket(
        api, ws.agent, ws.org_id, customer_id=customer["id"], priority="low"
    )
    assert await clocks(api, ws, ticket) == {"configured": True, "clocks": []}

    clock.set(2026, 11, 16, 11, 0)
    await act(api, ws, ticket, priority="high")
    first = current(await clocks(api, ws, ticket), "first_response")
    assert local(first["started_at"]) == datetime(2026, 11, 16, 11, 0)
    assert local(first["due_at"]) == datetime(2026, 11, 16, 13, 0)


async def test_running_clocks_keep_their_targets_and_calendar(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    normal = next(p for p in sla["policies"] if p["priority"] == "normal")
    admin = in_org(ws.admin, ws.org_id)
    await api.patch(
        f"{API}/sla/policies/{normal['id']}", json={"first_response_minutes": 90}, headers=admin
    )
    await api.put(
        f"{API}/sla/calendar",
        json={
            "timezone": "Asia/Kathmandu",
            "hours": [{"weekday": d, "start": "00:00", "end": "00:00"} for d in range(1, 8)],
        },
        headers=admin,
    )

    first = current(await clocks(api, ws, ticket), "first_response")
    assert (first["target_minutes"], first["policy_version"]) == (240, 1)
    assert local(first["due_at"]) == datetime(2026, 11, 16, 13, 0)

    newer = current(
        await clocks(api, ws, await create_ticket(api, ws.customer, ws.org_id)), "first_response"
    )
    assert (newer["target_minutes"], newer["policy_version"]) == (90, 2)
    assert local(newer["due_at"]) == datetime(2026, 11, 16, 10, 30)  # round the clock now


async def test_customers_cannot_see_slas(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    response = await api.get(
        f"{API}/tickets/{ticket['id']}/sla", headers=in_org(ws.customer, ws.org_id)
    )
    assert response.status_code == 403
    await set_rls_context(
        db_session,
        organization_id=uuid.UUID(ws.org_id),
        user_id=uuid.UUID(ws.customer.id),
        bypass=False,
    )
    count = (await db_session.execute(text("SELECT count(*) FROM ticket_sla_clocks"))).scalar_one()
    assert count == 0


async def test_auto_close_cancels_an_unanswered_first_response(
    api: httpx2.AsyncClient, ws: Workspace, clock: Clock, sla: dict, db_session: AsyncSession
) -> None:
    from app.services.automation import auto_close_resolved

    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await act(api, ws, ticket, assignee_id=ws.agent.id, status="in_progress")
    await act(api, ws, ticket, status="resolved")
    async with rls_bypass(db_session):
        await db_session.execute(
            text("UPDATE tickets SET resolved_at = now() - interval '30 days' WHERE id = :id"),
            {"id": uuid.UUID(ticket["id"])},
        )
    await db_session.commit()
    assert await auto_close_resolved(db_session, now=datetime.now(UTC) + timedelta(seconds=1)) == 1
    statuses = {c["kind"]: c["status"] for c in (await clocks(api, ws, ticket))["clocks"]}
    assert statuses == {"first_response": "cancelled", "resolution": "met"}
