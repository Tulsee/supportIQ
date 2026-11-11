from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import rls_bypass
from tests.factories import (
    API,
    AuthedUser,
    Outbox,
    Workspace,
    add_member,
    create_customer,
    create_org,
    create_ticket,
    create_user,
    in_org,
)

pytestmark = pytest.mark.anyio

TICKETS = f"{API}/tickets"


async def patch(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, actor: AuthedUser | None = None, **body
) -> httpx2.Response:
    return await api.patch(
        f"{TICKETS}/{ticket['id']}", json=body, headers=in_org(actor or ws.agent, ws.org_id)
    )


async def reply(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict, actor: AuthedUser, body: str, **extra
) -> httpx2.Response:
    return await api.post(
        f"{TICKETS}/{ticket['id']}/messages",
        json={"body": body, **extra},
        headers=in_org(actor, ws.org_id),
    )


async def listing(
    api: httpx2.AsyncClient, ws: Workspace, actor: AuthedUser, **params: Any
) -> dict[str, Any]:
    response = await api.get(TICKETS, params=params, headers=in_org(actor, ws.org_id))
    assert response.status_code == 200, response.text
    return response.json()


async def staff_ticket(api: httpx2.AsyncClient, ws: Workspace, **extra: Any) -> dict[str, Any]:
    customer = await create_customer(api, ws.agent, ws.org_id)
    return await create_ticket(api, ws.agent, ws.org_id, customer_id=customer["id"], **extra)


# --- Creating --------------------------------------------------------------------------


async def test_ticket_numbers_are_sequential_per_organization(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    first = await staff_ticket(api, ws)
    second = await staff_ticket(api, ws)
    other_admin = await create_user(api)
    other_org = await create_org(api, other_admin, "Other Co")
    other_customer = await create_customer(api, other_admin, other_org["id"])
    other = await create_ticket(api, other_admin, other_org["id"], customer_id=other_customer["id"])

    assert (first["number"], second["number"]) == (1, 2)
    assert other["number"] == 1
    assert first["status"] == "open"
    assert first["priority"] == "normal"
    assert first["assignee"] is None
    assert first["allowed_statuses"] == []  # open tickets move on by being assigned


async def test_staff_must_name_an_existing_customer(api: httpx2.AsyncClient, ws: Workspace) -> None:
    missing = await api.post(
        TICKETS, json={"subject": "S", "message": "M"}, headers=in_org(ws.agent, ws.org_id)
    )
    other_admin = await create_user(api)
    other_org = await create_org(api, other_admin, "Other Co")
    foreign = await create_customer(api, other_admin, other_org["id"])
    wrong_org = await api.post(
        TICKETS,
        json={"subject": "S", "message": "M", "customer_id": foreign["id"]},
        headers=in_org(ws.agent, ws.org_id),
    )

    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "customer_required"
    assert wrong_org.status_code == 400
    assert wrong_org.json()["error"]["code"] == "invalid_customer"


async def test_customer_opens_a_ticket_for_themselves(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Refund please")

    assert ticket["customer"]["email"] == ws.customer.email
    assert ticket["customer"]["name"] == "Cleo Customer"
    customers = (await api.get(f"{API}/customers", headers=in_org(ws.agent, ws.org_id))).json()
    assert [c["has_account"] for c in customers["items"]] == [True]
    # A second ticket reuses the same customer record.
    again = await create_ticket(api, ws.customer, ws.org_id)
    assert again["customer"]["id"] == ticket["customer"]["id"]


async def test_customer_account_links_to_record_staff_created_earlier(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    record = await create_customer(api, ws.agent, ws.org_id, "Cleo (phone)", ws.customer.email)
    logged_by_staff = await create_ticket(
        api, ws.agent, ws.org_id, customer_id=record["id"], subject="Called about billing"
    )

    own = await create_ticket(api, ws.customer, ws.org_id)
    portal = await listing(api, ws, ws.customer)

    assert own["customer"]["id"] == record["id"]
    assert {t["id"] for t in portal["items"]} == {logged_by_staff["id"], own["id"]}


async def test_customers_cannot_set_staff_fields(api: httpx2.AsyncClient, ws: Workspace) -> None:
    response = await api.post(
        TICKETS,
        json={"subject": "S", "message": "M", "priority": "urgent"},
        headers=in_org(ws.customer, ws.org_id),
    )

    assert response.status_code == 403


# --- Lifecycle -------------------------------------------------------------------------


async def test_full_lifecycle_with_customer_replies(api: httpx2.AsyncClient, ws: Workspace) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    assigned = await patch(api, ws, ticket, assignee_id=ws.agent.id)
    assert assigned.json()["status"] == "assigned"
    assert assigned.json()["assignee"]["full_name"] == "Abe Agent"
    assert assigned.json()["allowed_statuses"] == ["in_progress"]

    assert (await patch(api, ws, ticket, status="in_progress")).json()["status"] == "in_progress"
    waiting = await patch(api, ws, ticket, status="waiting_for_customer")
    assert waiting.json()["status"] == "waiting_for_customer"

    # The customer's reply puts it back in progress.
    assert (await reply(api, ws, ticket, ws.customer, "Here is the info")).status_code == 201
    current = (
        await api.get(f"{TICKETS}/{ticket['id']}", headers=in_org(ws.agent, ws.org_id))
    ).json()
    assert current["status"] == "in_progress"

    resolved = (await patch(api, ws, ticket, status="resolved")).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None
    assert resolved["allowed_statuses"] == ["in_progress", "closed"]

    # Replying to a resolved ticket reopens it.
    await reply(api, ws, ticket, ws.customer, "Still broken")
    reopened = (
        await api.get(f"{TICKETS}/{ticket['id']}", headers=in_org(ws.agent, ws.org_id))
    ).json()
    assert reopened["status"] == "in_progress"
    assert reopened["resolved_at"] is None

    await patch(api, ws, ticket, status="resolved")
    closed = (await patch(api, ws, ticket, status="closed")).json()
    assert closed["status"] == "closed"
    assert closed["closed_at"] is not None
    assert closed["allowed_statuses"] == []

    # Closed is final.
    late_reply = await reply(api, ws, ticket, ws.customer, "One more thing")
    late_change = await patch(api, ws, ticket, priority="high")
    assert late_reply.status_code == late_change.status_code == 409
    assert late_reply.json()["error"]["code"] == "ticket_closed"


async def test_invalid_transitions_are_rejected(api: httpx2.AsyncClient, ws: Workspace) -> None:
    ticket = await staff_ticket(api, ws)

    skip_ahead = await patch(api, ws, ticket, status="resolved")
    no_assignee = await patch(api, ws, ticket, status="assigned")

    assert skip_ahead.status_code == 409
    error = skip_ahead.json()["error"]
    assert error["code"] == "invalid_status_transition"
    assert error["details"] == {"from": "open", "to": "resolved", "allowed": ["assigned"]}
    assert no_assignee.status_code == 409
    unchanged = (
        await api.get(f"{TICKETS}/{ticket['id']}", headers=in_org(ws.agent, ws.org_id))
    ).json()
    assert unchanged["status"] == "open"


async def test_assign_and_start_in_one_request(api: httpx2.AsyncClient, ws: Workspace) -> None:
    ticket = await staff_ticket(api, ws)

    response = await patch(api, ws, ticket, assignee_id=ws.admin.id, status="in_progress")

    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert response.json()["assignee"]["id"] == ws.admin.id


async def test_unassigning_an_assigned_ticket_returns_it_to_the_queue(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await staff_ticket(api, ws, assignee_id=ws.agent.id)
    assert ticket["status"] == "assigned"

    response = await patch(api, ws, ticket, assignee_id=None)

    assert response.json()["status"] == "open"
    assert response.json()["assignee"] is None


async def test_only_staff_of_the_organization_can_be_assigned(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await staff_ticket(api, ws)
    outsider = await create_user(api)

    to_customer = await patch(api, ws, ticket, assignee_id=ws.customer.id)
    to_outsider = await patch(api, ws, ticket, assignee_id=outsider.id)

    assert to_customer.status_code == to_outsider.status_code == 400
    assert to_customer.json()["error"]["code"] == "invalid_assignee"


async def test_customers_cannot_update_tickets(api: httpx2.AsyncClient, ws: Workspace) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    response = await patch(api, ws, ticket, actor=ws.customer, priority="urgent")

    assert response.status_code == 403


async def test_removing_an_agent_returns_their_tickets_to_the_queue(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await staff_ticket(api, ws, assignee_id=ws.agent.id)
    members = (
        await api.get(f"{API}/organizations/{ws.org_id}/members", headers=ws.admin.headers)
    ).json()
    agent_member = next(m for m in members["items"] if m["user_id"] == ws.agent.id)

    removed = await api.delete(
        f"{API}/organizations/{ws.org_id}/members/{agent_member['id']}", headers=ws.admin.headers
    )

    assert removed.status_code == 204
    after = (await api.get(f"{TICKETS}/{ticket['id']}", headers=in_org(ws.admin, ws.org_id))).json()
    assert after["assignee"] is None
    assert after["status"] == "open"


# --- Messages and the timeline ------------------------------------------------------------


async def test_internal_notes_are_hidden_from_customers(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, message="My order is late")
    await reply(api, ws, ticket, ws.agent, "Checking with the warehouse")
    note = await reply(api, ws, ticket, ws.agent, "Customer is a VIP", is_internal=True)

    staff_view = (
        await api.get(f"{TICKETS}/{ticket['id']}/messages", headers=in_org(ws.agent, ws.org_id))
    ).json()
    customer_view = (
        await api.get(f"{TICKETS}/{ticket['id']}/messages", headers=in_org(ws.customer, ws.org_id))
    ).json()

    assert note.json()["is_internal"] is True
    assert [m["body"] for m in staff_view["items"]] == [
        "My order is late",
        "Checking with the warehouse",
        "Customer is a VIP",
    ]
    assert [m["author_type"] for m in staff_view["items"]] == ["customer", "agent", "agent"]
    assert [m["body"] for m in customer_view["items"]] == [
        "My order is late",
        "Checking with the warehouse",
    ]
    assert customer_view["total"] == 2


async def test_customers_cannot_post_internal_notes(api: httpx2.AsyncClient, ws: Workspace) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    response = await reply(api, ws, ticket, ws.customer, "sneaky", is_internal=True)

    assert response.status_code == 403


async def test_timeline_shows_customers_only_status_history(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await patch(api, ws, ticket, assignee_id=ws.agent.id, priority="high")

    staff_events = (
        await api.get(f"{TICKETS}/{ticket['id']}/events", headers=in_org(ws.agent, ws.org_id))
    ).json()
    customer_events = (
        await api.get(f"{TICKETS}/{ticket['id']}/events", headers=in_org(ws.customer, ws.org_id))
    ).json()

    assert [e["type"] for e in staff_events] == [
        "created",
        "priority_changed",
        "assigned",
        "status_changed",
    ]
    assigned = staff_events[2]
    assert assigned["data"] == {"from": None, "to": {"id": ws.agent.id, "name": "Abe Agent"}}
    assert assigned["actor"]["full_name"] == "Abe Agent"
    assert [e["type"] for e in customer_events] == ["created", "status_changed"]
    assert customer_events[1]["data"] == {"from": "open", "to": "assigned"}


# --- Access ---------------------------------------------------------------------------------


async def test_customers_only_see_their_own_tickets(
    api: httpx2.AsyncClient, ws: Workspace, outbox: Outbox
) -> None:
    other_customer = await create_user(api, full_name="Otto Other")
    await add_member(api, outbox, ws.admin, ws.org_id, other_customer, "customer")
    mine = await create_ticket(api, ws.customer, ws.org_id)
    theirs = await create_ticket(api, other_customer, ws.org_id)
    staff_logged = await staff_ticket(api, ws)

    portal = await listing(api, ws, ws.customer)
    peek = await api.get(f"{TICKETS}/{theirs['id']}", headers=in_org(ws.customer, ws.org_id))
    peek_messages = await api.get(
        f"{TICKETS}/{theirs['id']}/messages", headers=in_org(ws.customer, ws.org_id)
    )
    staff_view = await listing(api, ws, ws.agent)

    assert [t["id"] for t in portal["items"]] == [mine["id"]]
    assert portal["items"][0]["allowed_statuses"] == []
    assert peek.status_code == peek_messages.status_code == 404
    assert staff_view["total"] == 3
    assert staff_logged["id"] in {t["id"] for t in staff_view["items"]}


async def test_tickets_of_another_organization_are_not_found(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await staff_ticket(api, ws)
    intruder = await create_user(api)
    intruder_org = await create_org(api, intruder, "Intruder Co")
    headers = in_org(intruder, intruder_org["id"])

    assert (await api.get(f"{TICKETS}/{ticket['id']}", headers=headers)).status_code == 404
    assert (
        await api.post(f"{TICKETS}/{ticket['id']}/messages", json={"body": "hi"}, headers=headers)
    ).status_code == 404
    assert (await api.get(TICKETS, headers=headers)).json()["total"] == 0
    # Naming the organization without being a member doesn't help either.
    assert (await api.get(TICKETS, headers=in_org(intruder, ws.org_id))).status_code == 404


async def test_deleting_an_organization_removes_its_tickets(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    """Customers and tickets both cascade from the organization; the
    ticket -> customer reference must not block that cascade midway."""
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await reply(api, ws, ticket, ws.agent, "hello")

    async with rls_bypass(db_session):
        await db_session.execute(
            text("DELETE FROM organizations WHERE id = :id"), {"id": ws.org_id}
        )
        left = await db_session.scalar(
            text("SELECT count(*) FROM tickets WHERE organization_id = :id"), {"id": ws.org_id}
        )

    assert left == 0


async def test_a_customer_with_tickets_cannot_be_deleted_directly(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)

    with pytest.raises(IntegrityError):
        async with rls_bypass(db_session), db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM customers WHERE id = :id"), {"id": ticket["customer"]["id"]}
            )


# --- Listing ----------------------------------------------------------------------------------


async def test_filters_search_and_counts(api: httpx2.AsyncClient, ws: Workspace) -> None:
    acme = await create_customer(api, ws.agent, ws.org_id, "Acme Corp", "ops@acme.example")
    printer = await create_ticket(
        api,
        ws.agent,
        ws.org_id,
        customer_id=acme["id"],
        subject="Printer jams constantly",
        message="Paper tray two",
        priority="urgent",
    )
    invoice = await create_ticket(
        api,
        ws.agent,
        ws.org_id,
        customer_id=acme["id"],
        subject="Invoice question",
        message="Why was I charged twice?",
        assignee_id=ws.agent.id,
    )
    other = await staff_ticket(api, ws, subject="Password reset", priority="low")

    def ids(page: dict) -> list[str]:
        return [t["id"] for t in page["items"]]

    assert ids(await listing(api, ws, ws.agent, assignee="me")) == [invoice["id"]]
    assert set(ids(await listing(api, ws, ws.agent, assignee="unassigned"))) == {
        printer["id"],
        other["id"],
    }
    assert ids(await listing(api, ws, ws.agent, status=["assigned"])) == [invoice["id"]]
    assert ids(await listing(api, ws, ws.agent, priority=["urgent", "high"])) == [printer["id"]]
    assert ids(await listing(api, ws, ws.agent, search="printers")) == [printer["id"]]  # stems
    assert ids(await listing(api, ws, ws.agent, search="charged")) == [invoice["id"]]  # body
    assert ids(await listing(api, ws, ws.agent, search=f"#{other['number']}")) == [other["id"]]
    assert set(ids(await listing(api, ws, ws.agent, search="acme"))) == {
        printer["id"],
        invoice["id"],
    }
    by_priority = await listing(api, ws, ws.agent, sort="-priority")
    assert ids(by_priority)[0] == printer["id"]
    assert ids(by_priority)[-1] == other["id"]

    counts = (await api.get(f"{TICKETS}/counts", headers=in_org(ws.agent, ws.org_id))).json()
    assert counts["by_status"]["open"] == 2
    assert counts["by_status"]["assigned"] == 1
    assert counts["unassigned"] == 2
    assert counts["assigned_to_me"] == 1
    customer_counts = await api.get(f"{TICKETS}/counts", headers=in_org(ws.customer, ws.org_id))
    assert customer_counts.status_code == 403


async def test_customer_search_never_matches_internal_notes(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Late order")
    await reply(api, ws, ticket, ws.agent, "flagged as fraudulent", is_internal=True)

    staff_hits = await listing(api, ws, ws.agent, search="fraudulent")
    customer_hits = await listing(api, ws, ws.customer, search="fraudulent")

    assert staff_hits["total"] == 1
    assert customer_hits["total"] == 0


async def test_new_messages_bring_tickets_to_the_top(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    first = await staff_ticket(api, ws, subject="First")
    second = await staff_ticket(api, ws, subject="Second")

    async def inbox_order() -> list[str]:
        return [t["id"] for t in (await listing(api, ws, ws.agent))["items"]]

    await reply(api, ws, first, ws.agent, "bump")
    assert await inbox_order() == [first["id"], second["id"]]

    await reply(api, ws, second, ws.agent, "bump")
    assert await inbox_order() == [second["id"], first["id"]]
