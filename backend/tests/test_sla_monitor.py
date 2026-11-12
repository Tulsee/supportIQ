"""The SLA monitor (Module 10, phase 4): due-soon and breach transitions recorded once,
timeline events for staff, notifications to the right people, skipped clocks, and
SLA compliance in analytics. Kathmandu, Monday-Friday 09:00-17:00, starting Monday
2026-11-16 09:00 (a normal ticket's first response is due 13:00, warning at 12:00)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sla_monitor import evaluate_sla_clocks
from app.websocket.broker import InMemoryEventBroker
from tests.factories import API, RecordingMailer, Workspace, create_ticket, in_org

pytestmark = pytest.mark.anyio

KTM = ZoneInfo("Asia/Kathmandu")


def at(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=KTM).astimezone(UTC)


@pytest.fixture(autouse=True)
def frozen_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clocks start Monday 09:00 (the monitor gets its time explicitly)."""
    monkeypatch.setattr("app.services.sla_clocks.utcnow", lambda: at(2026, 11, 16, 9, 0))


@pytest.fixture
async def sla(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await api.post(
        f"{API}/sla/setup", json={"timezone": "Asia/Kathmandu"}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text


async def notices(api: httpx2.AsyncClient, ws: Workspace, user) -> list[tuple[str, str]]:
    items = (await api.get(f"{API}/notifications", headers=in_org(user, ws.org_id))).json()["items"]
    return [(i["kind"], i["title"]) for i in items if i["kind"].startswith("sla_")]


async def events(api: httpx2.AsyncClient, ws: Workspace, ticket: dict, user) -> list[str]:
    response = await api.get(
        f"{API}/tickets/{ticket['id']}/events", headers=in_org(user, ws.org_id)
    )
    return [e["type"] for e in response.json() if e["type"].startswith("sla_")]


async def test_due_soon_then_breach_are_recorded_once_and_notified(
    api: httpx2.AsyncClient,
    ws: Workspace,
    sla: None,
    db_session: AsyncSession,
    mailer: RecordingMailer,
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Router down")
    await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": ws.agent.id},
        headers=in_org(ws.admin, ws.org_id),
    )
    broker = InMemoryEventBroker()

    async def run(*parts: int):
        return await evaluate_sla_clocks(db_session, now=at(*parts), events=broker, mailer=mailer)

    result = await run(2026, 11, 16, 11, 59)
    assert (result.at_risk, result.breached) == (0, 0)

    result = await run(2026, 11, 16, 12, 5)
    assert (result.at_risk, result.breached) == (1, 0)
    number = ticket["number"]
    assert await notices(api, ws, ws.agent) == [
        ("sla_at_risk", f"First response due soon on #{number}")
    ]
    assert await notices(api, ws, ws.admin) == []  # the assignee is on it
    assert await events(api, ws, ticket, ws.agent) == ["sla_at_risk"]
    # Open ticket views refresh (staff only), and the agent's bell is pinged.
    published = [(e.type, e.staff_only, e.recipient_user_id) for e in broker.published]
    assert published == [
        ("ticket.updated", True, None),
        ("notification.created", False, ws.agent.id),
    ]

    result = await run(2026, 11, 16, 12, 30)  # nothing new
    assert (result.at_risk, result.breached) == (0, 0)

    result = await run(2026, 11, 16, 13, 5)
    assert (result.at_risk, result.breached) == (0, 1)
    breach = ("sla_breached", f"SLA breached: first response on #{number}")
    assert (await notices(api, ws, ws.agent))[0] == breach
    assert await notices(api, ws, ws.admin) == [breach]
    assert await events(api, ws, ticket, ws.agent) == ["sla_at_risk", "sla_breached"]
    assert await events(api, ws, ticket, ws.customer) == []  # staff only
    # Breaches are emailed by default (to the agent and the admin).
    assert len(mailer.ids) == 2

    clock = (
        await api.get(f"{API}/tickets/{ticket['id']}/sla", headers=in_org(ws.agent, ws.org_id))
    ).json()["clocks"][0]
    assert clock["kind"] == "first_response" and clock["status"] == "breached"
    assert datetime.fromisoformat(clock["breached_at"]) == at(2026, 11, 16, 13, 0)


async def test_unassigned_tickets_alert_the_admins(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await evaluate_sla_clocks(db_session, now=at(2026, 11, 16, 12, 5))
    assert await notices(api, ws, ws.admin) == [
        ("sla_at_risk", f"First response due soon on #{ticket['number']}")
    ]
    assert await notices(api, ws, ws.agent) == []


async def test_met_and_paused_clocks_are_left_alone(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    headers = in_org(ws.agent, ws.org_id)
    await api.post(
        f"{API}/tickets/{ticket['id']}/messages", json={"body": "On it"}, headers=headers
    )
    await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": ws.agent.id, "status": "in_progress"},
        headers=headers,
    )
    await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"status": "waiting_for_customer"}, headers=headers
    )

    # A week later: the first response was met, the resolution clock is paused.
    result = await evaluate_sla_clocks(db_session, now=at(2026, 11, 23, 12, 0))
    assert (result.at_risk, result.breached) == (0, 0)


async def test_a_late_run_records_the_breach_without_a_due_soon_alert(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    # The scheduler was down all Monday: first response long overdue, resolution near.
    result = await evaluate_sla_clocks(db_session, now=at(2026, 11, 18, 16, 30))
    assert (result.at_risk, result.breached) == (1, 1)
    assert sorted(await events(api, ws, ticket, ws.agent)) == ["sla_at_risk", "sla_breached"]
    kinds = sorted(k for k, _ in await notices(api, ws, ws.admin))
    assert kinds == ["sla_at_risk", "sla_breached"]  # one per clock, not two for the first


async def test_analytics_report_compliance(
    api: httpx2.AsyncClient, ws: Workspace, sla: None, db_session: AsyncSession
) -> None:
    breached = await create_ticket(api, ws.customer, ws.org_id, subject="Overdue")
    await create_ticket(api, ws.customer, ws.org_id, subject="Still in time")
    await evaluate_sla_clocks(db_session, now=at(2026, 11, 16, 13, 5))
    # A late answer: met, but late.
    await api.post(
        f"{API}/tickets/{breached['id']}/messages",
        json={"body": "Sorry"},
        headers=in_org(ws.agent, ws.org_id),
    )

    sla = (await api.get(f"{API}/analytics/overview", headers=in_org(ws.admin, ws.org_id))).json()[
        "sla"
    ]
    assert sla["configured"] is True
    first = sla["first_response"]
    # The second ticket's first response also passed 13:00 when the monitor ran.
    assert (first["on_time"], first["late"], first["overdue"]) == (0, 1, 1)
    assert first["rate"] == 0.0
    assert sla["resolution"]["open"] == 2 and sla["resolution"]["rate"] is None


def test_the_scheduler_runs_the_monitor() -> None:
    from app.workers import tasks  # noqa: F401  (registers the tasks)
    from app.workers.celery_app import celery_app

    job = celery_app.conf.beat_schedule["sla-monitor"]
    assert job["task"] == "sla.evaluate" and job["task"] in celery_app.tasks
    assert job["schedule"] == 60
