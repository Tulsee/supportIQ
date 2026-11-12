"""Ticket categories: admins manage them, staff set them on tickets, customers never
see them."""

import uuid

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_rls_context
from tests.factories import API, Workspace, create_org, create_ticket, create_user, in_org

pytestmark = pytest.mark.anyio

CATEGORIES = f"{API}/categories"


async def add_category(api: httpx2.AsyncClient, ws: Workspace, name: str, **extra) -> dict:
    response = await api.post(
        CATEGORIES, json={"name": name, **extra}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_admins_manage_categories_and_staff_read_them(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    billing = await add_category(api, ws, "Billing & refunds", description="Money questions")
    assert billing["key"] == "billing-refunds"
    assert billing["is_active"] is True
    await add_category(api, ws, "Wi-Fi", key="wifi")

    duplicate = await api.post(
        CATEGORIES,
        json={"name": "Billing", "key": "billing-refunds"},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "category_exists"

    agent_list = await api.get(CATEGORIES, headers=in_org(ws.agent, ws.org_id))
    assert [c["name"] for c in agent_list.json()] == ["Billing & refunds", "Wi-Fi"]
    for actor, method, url in (
        (ws.agent, "post", CATEGORIES),
        (ws.customer, "get", CATEGORIES),
        (ws.agent, "patch", f"{CATEGORIES}/{billing['id']}"),
    ):
        response = await api.request(
            method.upper(), url, json={"name": "Nope"}, headers=in_org(actor, ws.org_id)
        )
        assert response.status_code == 403, (actor, method)

    deactivated = await api.patch(
        f"{CATEGORIES}/{billing['id']}",
        json={"is_active": False},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert deactivated.json()["is_active"] is False
    active = await api.get(CATEGORIES, headers=in_org(ws.agent, ws.org_id))
    assert [c["key"] for c in active.json()] == ["wifi"]
    everything = await api.get(
        CATEGORIES, params={"include_inactive": True}, headers=in_org(ws.agent, ws.org_id)
    )
    assert len(everything.json()) == 2

    log = (await api.get(f"{API}/audit-log", headers=in_org(ws.admin, ws.org_id))).json()["items"]
    assert [e["action"] for e in log[:3]] == [
        "category.updated",
        "category.created",
        "category.created",
    ]
    assert log[0]["summary"] == "Deactivated the ticket category Billing & refunds"


async def test_staff_categorize_tickets_and_filter_by_category(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    billing = await add_category(api, ws, "Billing")
    wifi = await add_category(api, ws, "Wi-Fi")
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Charged twice")
    other = await create_ticket(api, ws.customer, ws.org_id, subject="Slow internet")
    headers = in_org(ws.agent, ws.org_id)

    categorized = await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"category_id": billing["id"]}, headers=headers
    )
    assert categorized.status_code == 200, categorized.text
    assert categorized.json()["category"] == {
        "id": billing["id"],
        "key": "billing",
        "name": "Billing",
    }
    await api.patch(
        f"{API}/tickets/{other['id']}", json={"category_id": wifi["id"]}, headers=headers
    )

    filtered = await api.get(
        f"{API}/tickets", params={"category_id": billing["id"]}, headers=headers
    )
    assert [t["subject"] for t in filtered.json()["items"]] == ["Charged twice"]

    events = (await api.get(f"{API}/tickets/{ticket['id']}/events", headers=headers)).json()
    changed = [e for e in events if e["type"] == "category_changed"]
    assert changed[0]["data"] == {"from": None, "to": {"id": billing["id"], "name": "Billing"}}

    # Customers see neither the category nor the change.
    mine = await api.get(f"{API}/tickets/{ticket['id']}", headers=in_org(ws.customer, ws.org_id))
    assert mine.json()["category"] is None
    customer_events = (
        await api.get(
            f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.customer, ws.org_id)
        )
    ).json()
    assert "category_changed" not in [e["type"] for e in customer_events]

    cleared = await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"category_id": None}, headers=headers
    )
    assert cleared.json()["category"] is None


async def test_only_active_categories_of_the_organization_can_be_set(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    retired = await add_category(api, ws, "Retired")
    await api.patch(
        f"{CATEGORIES}/{retired['id']}",
        json={"is_active": False},
        headers=in_org(ws.admin, ws.org_id),
    )
    outsider = await create_user(api)
    other_org = await create_org(api, outsider)
    foreign = await api.post(
        CATEGORIES, json={"name": "Elsewhere"}, headers=in_org(outsider, other_org["id"])
    )
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    for category_id in (retired["id"], foreign.json()["id"], str(uuid.uuid4())):
        response = await api.patch(
            f"{API}/tickets/{ticket['id']}",
            json={"category_id": category_id},
            headers=in_org(ws.agent, ws.org_id),
        )
        assert response.status_code == 400, category_id
        assert response.json()["error"]["code"] == "invalid_category"

    as_customer = await api.post(
        f"{API}/tickets",
        json={"subject": "Hi", "message": "Help", "category_id": retired["id"]},
        headers=in_org(ws.customer, ws.org_id),
    )
    assert as_customer.status_code == 403


async def test_customers_cannot_read_categories_in_the_database(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    await add_category(api, ws, "Billing")
    org = uuid.UUID(ws.org_id)

    async def visible_to(user) -> int:
        await set_rls_context(
            db_session, organization_id=org, user_id=uuid.UUID(user.id), bypass=False
        )
        return (
            await db_session.execute(text("SELECT count(*) FROM ticket_categories"))
        ).scalar_one()

    assert await visible_to(ws.agent) == 1
    assert await visible_to(ws.customer) == 0
