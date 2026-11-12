"""Scheduled automation: auto-closing resolved tickets, its per-organization setting,
and the beat schedule."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from app.services.automation import auto_close_resolved
from app.websocket.broker import InMemoryEventBroker
from tests.factories import API, Workspace, create_ticket, in_org

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


async def resolved_ticket(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession, days_ago: float
) -> dict:
    ticket = await create_ticket(
        api, ws.customer, ws.org_id, subject=f"Resolved {days_ago} days ago"
    )
    headers = in_org(ws.agent, ws.org_id)
    for change in ({"assignee_id": ws.agent.id}, {"status": "in_progress"}, {"status": "resolved"}):
        response = await api.patch(f"{API}/tickets/{ticket['id']}", json=change, headers=headers)
        assert response.status_code == 200, response.text
    async with rls_bypass(db_session):
        await db_session.execute(
            text("UPDATE tickets SET resolved_at = :at WHERE id = :id"),
            {"at": NOW - timedelta(days=days_ago), "id": uuid.UUID(ticket["id"])},
        )
    await db_session.commit()
    return ticket


async def status_of(api: httpx2.AsyncClient, ws: Workspace, ticket: dict) -> str:
    response = await api.get(f"{API}/tickets/{ticket['id']}", headers=in_org(ws.agent, ws.org_id))
    return response.json()["status"]


async def test_resolved_tickets_close_after_the_default_week(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    stale = await resolved_ticket(api, ws, db_session, days_ago=8)
    recent = await resolved_ticket(api, ws, db_session, days_ago=6)
    broker = InMemoryEventBroker()

    assert await auto_close_resolved(db_session, now=NOW, events=broker) == 1

    assert await status_of(api, ws, stale) == "closed"
    assert await status_of(api, ws, recent) == "resolved"
    events = (
        await api.get(f"{API}/tickets/{stale['id']}/events", headers=in_org(ws.customer, ws.org_id))
    ).json()
    assert events[-1]["type"] == "status_changed"
    assert events[-1]["actor"] is None
    assert events[-1]["data"] == {
        "from": "resolved",
        "to": "closed",
        "automatic": True,
        "after_days": 7,
    }
    assert [(e.type, e.ticket_id) for e in broker.published] == [("ticket.updated", stale["id"])]
    # Nothing left to do: running again closes nothing.
    assert await auto_close_resolved(db_session, now=NOW) == 0


async def test_each_organization_chooses_its_window(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    ticket = await resolved_ticket(api, ws, db_session, days_ago=20)
    org_url = f"{API}/organizations/{ws.org_id}"

    never = await api.patch(
        org_url, json={"settings": {"auto_close_after_days": 0}}, headers=ws.admin.headers
    )
    assert never.status_code == 200, never.text
    assert never.json()["settings"] == {"auto_close_after_days": 0}
    assert await auto_close_resolved(db_session, now=NOW) == 0
    assert await status_of(api, ws, ticket) == "resolved"

    month = await api.patch(
        org_url, json={"settings": {"auto_close_after_days": 30}}, headers=ws.admin.headers
    )
    assert month.json()["settings"]["auto_close_after_days"] == 30
    assert month.json()["name"]  # unchanged
    assert await auto_close_resolved(db_session, now=NOW) == 0
    assert await auto_close_resolved(db_session, now=NOW + timedelta(days=11)) == 1
    assert await status_of(api, ws, ticket) == "closed"

    log = (await api.get(f"{API}/audit-log", headers=in_org(ws.admin, ws.org_id))).json()["items"]
    assert log[0]["action"] == "organization.settings_changed"
    assert log[0]["changes"] == {"auto_close_after_days": {"from": 0, "to": 30}}


async def test_only_admins_change_settings_within_limits(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    org_url = f"{API}/organizations/{ws.org_id}"
    as_agent = await api.patch(
        org_url, json={"settings": {"auto_close_after_days": 3}}, headers=ws.agent.headers
    )
    too_long = await api.patch(
        org_url, json={"settings": {"auto_close_after_days": 91}}, headers=ws.admin.headers
    )
    assert as_agent.status_code == 403
    assert too_long.status_code == 422
    default = await api.get(org_url, headers=ws.customer.headers)
    assert default.json()["settings"] == {"auto_close_after_days": 7}


def test_the_scheduler_runs_auto_close() -> None:
    from app.workers import tasks  # noqa: F401  (registers the tasks)
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule["auto-close-resolved-tickets"]
    assert schedule["task"] == "tickets.auto_close"
    assert schedule["task"] in celery_app.tasks
    assert schedule["schedule"] == 30 * 60
