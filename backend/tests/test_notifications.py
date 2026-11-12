"""Notifications (Module 9): who is told about what, preferences, read state,
isolation, idempotency, real-time pings and email."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.realtime import Subscriber
from app.core.database import set_rls_context
from app.models import NotificationKind
from app.services.notifications import NewNotification, NotificationService, send_pending_emails
from app.websocket.broker import InMemoryEventBroker, TicketEvent
from tests.factories import API, Outbox, RecordingMailer, Workspace, create_ticket, in_org

pytestmark = pytest.mark.anyio


async def notifications_of(api: httpx2.AsyncClient, user, org_id: str, **params: object) -> dict:
    response = await api.get(f"{API}/notifications", params=params, headers=in_org(user, org_id))
    assert response.status_code == 200, response.text
    return response.json()


async def assign(api: httpx2.AsyncClient, actor, ws: Workspace, ticket: dict, assignee) -> None:
    response = await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": assignee.id},
        headers=in_org(actor, ws.org_id),
    )
    assert response.status_code == 200, response.text


async def post_message(
    api: httpx2.AsyncClient, author, ws: Workspace, ticket: dict, body: str, **extra: object
) -> None:
    response = await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": body, **extra},
        headers=in_org(author, ws.org_id),
    )
    assert response.status_code == 201, response.text


async def test_assignment_notifies_the_assignee_but_not_the_actor(
    api: httpx2.AsyncClient, ws: Workspace, broker: InMemoryEventBroker
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Printer on fire")
    await assign(api, ws.admin, ws, ticket, ws.agent)

    agent = await notifications_of(api, ws.agent, ws.org_id)
    assert agent["unread"] == 1
    [item] = agent["items"]
    assert item["kind"] == "ticket_assigned"
    assert item["ticket_id"] == ticket["id"]
    assert item["title"] == f"#{ticket['number']} was assigned to you"
    assert item["body"] == "Printer on fire"
    assert (await notifications_of(api, ws.admin, ws.org_id))["total"] == 0
    pings = [e for e in broker.published if e.type == "notification.created"]
    assert [e.recipient_user_id for e in pings] == [ws.agent.id]

    # Taking a ticket yourself isn't news.
    other = await create_ticket(api, ws.customer, ws.org_id, subject="Second")
    await assign(api, ws.agent, ws, other, ws.agent)
    assert (await notifications_of(api, ws.agent, ws.org_id))["total"] == 1


async def test_replies_notify_the_other_side_and_internal_notes_nobody(
    api: httpx2.AsyncClient, ws: Workspace, mailer: RecordingMailer
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await assign(api, ws.admin, ws, ticket, ws.agent)

    await post_message(api, ws.agent, ws, ticket, "We're on it.")
    customer = await notifications_of(api, ws.customer, ws.org_id)
    [reply] = customer["items"]
    assert reply["kind"] == "agent_replied"
    assert reply["title"] == f"New reply on your request #{ticket['number']}"
    # Customers get replies by email too, by default.
    assert mailer.ids == [uuid.UUID(reply["id"])]

    await post_message(api, ws.agent, ws, ticket, "Customer sounds upset", is_internal=True)
    assert (await notifications_of(api, ws.customer, ws.org_id))["total"] == 1

    await post_message(api, ws.customer, ws, ticket, "Still broken.")
    agent = await notifications_of(api, ws.agent, ws.org_id, unread_only=True)
    assert [i["kind"] for i in agent["items"]] == ["customer_replied", "ticket_assigned"]
    assert agent["items"][0]["body"] == "Still broken."
    assert len(mailer.ids) == 1  # staff notifications are in the app only, by default


async def test_unassigned_tickets_notify_nobody_of_customer_replies(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await post_message(api, ws.customer, ws, ticket, "Anyone there?")
    for staff in (ws.admin, ws.agent):
        assert (await notifications_of(api, staff, ws.org_id))["total"] == 0


async def test_marking_notifications_read(api: httpx2.AsyncClient, ws: Workspace) -> None:
    for subject in ("First", "Second"):
        ticket = await create_ticket(api, ws.customer, ws.org_id, subject=subject)
        await assign(api, ws.admin, ws, ticket, ws.agent)
    items = (await notifications_of(api, ws.agent, ws.org_id))["items"]
    headers = in_org(ws.agent, ws.org_id)

    one = await api.post(
        f"{API}/notifications/read", json={"ids": [items[0]["id"]]}, headers=headers
    )
    assert one.json() == {"unread": 1}
    every = await api.post(f"{API}/notifications/read", json={"all": True}, headers=headers)
    assert every.json() == {"unread": 0}
    listing = await notifications_of(api, ws.agent, ws.org_id)
    assert listing["total"] == 2 and all(i["read_at"] for i in listing["items"])
    assert (await notifications_of(api, ws.agent, ws.org_id, unread_only=True))["total"] == 0

    for body in ({}, {"ids": [items[0]["id"]], "all": True}):
        bad = await api.post(f"{API}/notifications/read", json=body, headers=headers)
        assert bad.status_code == 422, body


async def test_preferences_turn_kinds_off_or_add_email(
    api: httpx2.AsyncClient, ws: Workspace, mailer: RecordingMailer
) -> None:
    agent_headers = in_org(ws.agent, ws.org_id)
    staff = (await api.get(f"{API}/notifications/preferences", headers=agent_headers)).json()
    assert staff["items"] == [
        {"kind": "ticket_assigned", "delivery": "in_app"},
        {"kind": "customer_replied", "delivery": "in_app"},
        {"kind": "ai_escalated", "delivery": "in_app"},
        {"kind": "ticket_closed", "delivery": "in_app"},
    ]
    customer = await api.get(
        f"{API}/notifications/preferences", headers=in_org(ws.customer, ws.org_id)
    )
    assert customer.json()["items"] == [{"kind": "agent_replied", "delivery": "email"}]

    for kind, delivery in (("ticket_assigned", "off"), ("customer_replied", "email")):
        changed = await api.put(
            f"{API}/notifications/preferences",
            json={"kind": kind, "delivery": delivery},
            headers=agent_headers,
        )
        assert changed.status_code == 200, changed.text
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await assign(api, ws.admin, ws, ticket, ws.agent)
    await post_message(api, ws.customer, ws, ticket, "Hello?")

    [item] = (await notifications_of(api, ws.agent, ws.org_id))["items"]
    assert item["kind"] == "customer_replied"
    assert mailer.ids == [uuid.UUID(item["id"])]

    wrong_role = await api.put(
        f"{API}/notifications/preferences",
        json={"kind": "ticket_assigned", "delivery": "off"},
        headers=in_org(ws.customer, ws.org_id),
    )
    assert wrong_role.status_code == 400
    assert wrong_role.json()["error"]["code"] == "invalid_kind"


async def test_people_only_see_their_own_notifications(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await assign(api, ws.admin, ws, ticket, ws.agent)
    org = uuid.UUID(ws.org_id)

    async def visible_to(user) -> int:
        await set_rls_context(
            db_session, organization_id=org, user_id=uuid.UUID(user.id), bypass=False
        )
        return (await db_session.execute(text("SELECT count(*) FROM notifications"))).scalar_one()

    assert await visible_to(ws.agent) == 1
    assert await visible_to(ws.admin) == 0
    assert await visible_to(ws.customer) == 0

    # Writing a notification for someone else needs the system bypass.
    await set_rls_context(
        db_session, organization_id=org, user_id=uuid.UUID(ws.customer.id), bypass=False
    )
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO notifications (id, organization_id, user_id, kind, title, "
                    "dedupe_key) VALUES (:id, :org, :user, 'ticket_assigned', 'x', 'forged')"
                ),
                {"id": uuid.uuid4(), "org": org, "user": uuid.UUID(ws.agent.id)},
            )


async def test_one_event_notifies_once(db_session: AsyncSession, ws: Workspace) -> None:
    org = uuid.UUID(ws.org_id)
    await set_rls_context(db_session, organization_id=org, user_id=uuid.UUID(ws.admin.id))
    service = NotificationService(db_session)
    item = NewNotification(
        user_id=uuid.UUID(ws.agent.id),
        kind=NotificationKind.TICKET_ASSIGNED,
        title="Assigned",
        dedupe_key="sla:clock-1:breached",
    )

    assert await service.add(org, [item]) == 1
    assert await service.add(org, [item]) == 0  # a repeated scan or a retried job
    assert await service.add(org, [item], actor_id=uuid.UUID(ws.agent.id)) == 0


async def test_emails_are_sent_once_with_a_link(
    api: httpx2.AsyncClient,
    ws: Workspace,
    db_session: AsyncSession,
    outbox: Outbox,
    mailer: RecordingMailer,
) -> None:
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Router won't start")
    await post_message(api, ws.agent, ws, ticket, "Try holding the reset button.")

    assert await send_pending_emails(db_session, outbox, mailer.ids) == 1
    email = outbox.last_to(ws.customer.email)
    assert email.subject == f"New reply on your request #{ticket['number']}"
    assert f"/tickets/{ticket['id']}" in email.text
    assert "Router won't start" in email.text
    assert await send_pending_emails(db_session, outbox, mailer.ids) == 0


def test_personal_events_reach_only_their_recipient() -> None:
    org, me, someone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    event = TicketEvent.build(
        "notification.created",
        organization_id=org,
        ticket_id=uuid.uuid4(),
        customer_user_id=None,
        recipient_user_id=me,
    )
    later = datetime(2100, 1, 1, tzinfo=UTC)

    def subscriber(user: uuid.UUID, *, staff: bool, ticket: uuid.UUID | None = None) -> Subscriber:
        return Subscriber(user, org, staff, ticket, later)

    assert subscriber(me, staff=False).wants(event)
    assert not subscriber(someone, staff=True).wants(event)  # not even other staff
    assert not subscriber(me, staff=True, ticket=uuid.uuid4()).wants(event)  # one-ticket socket
