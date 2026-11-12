import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx2
import jwt
import pytest
from redis.asyncio import Redis

from app.api.v1.endpoints import realtime
from app.core.config import settings
from app.core.security import JWT_ALGORITHM
from app.main import app
from app.websocket.broker import InMemoryEventBroker, RedisEventBroker, TicketEvent
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
from tests.ws import WebSocketSession

pytestmark = pytest.mark.anyio

STREAM = f"{API}/ws/tickets"


def token_of(user: AuthedUser) -> str:
    return user.headers["Authorization"].removeprefix("Bearer ")


@asynccontextmanager
async def stream(
    user: AuthedUser | None,
    org_id: str,
    ticket_id: str | None = None,
    *,
    token: str | None = None,
) -> AsyncIterator[WebSocketSession]:
    """An authenticated, ready subscription."""
    path = f"{STREAM}/{ticket_id}" if ticket_id else STREAM
    async with WebSocketSession(app, path) as ws:
        await ws.send_json(
            {"type": "auth", "token": token or token_of(user), "organization_id": org_id}
        )
        assert await ws.receive_json() == {"type": "ready"}
        yield ws


async def note(api: httpx2.AsyncClient, ws: Workspace, ticket: dict, body: str) -> None:
    response = await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": body, "is_internal": True},
        headers=in_org(ws.agent, ws.org_id),
    )
    assert response.status_code == 201


async def reply(api: httpx2.AsyncClient, ws: Workspace, ticket: dict, actor: AuthedUser) -> None:
    response = await api.post(
        f"{API}/tickets/{ticket['id']}/messages",
        json={"body": "hello"},
        headers=in_org(actor, ws.org_id),
    )
    assert response.status_code == 201


# --- What each subscriber hears --------------------------------------------------------------


async def test_staff_hear_every_change_in_the_organization(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    async with stream(ws.agent, ws.org_id) as socket:
        ticket = await create_ticket(api, ws.customer, ws.org_id)
        await note(api, ws, ticket, "internal")
        await api.patch(
            f"{API}/tickets/{ticket['id']}",
            json={"priority": "high"},
            headers=in_org(ws.agent, ws.org_id),
        )

        received = [await socket.receive_json() for _ in range(3)]

    assert received == [
        {"type": "ticket.created", "ticket_id": ticket["id"]},
        {"type": "message.created", "ticket_id": ticket["id"]},
        {"type": "ticket.updated", "ticket_id": ticket["id"]},
    ]


async def test_customers_hear_only_their_own_customer_visible_changes(
    api: httpx2.AsyncClient, ws: Workspace, outbox: Outbox
) -> None:
    other = await create_user(api, full_name="Otto Other")
    await add_member(api, outbox, ws.admin, ws.org_id, other, "customer")
    mine = await create_ticket(api, ws.customer, ws.org_id)
    theirs = await create_ticket(api, other, ws.org_id)

    async with stream(ws.customer, ws.org_id) as socket:
        await reply(api, ws, theirs, ws.agent)  # someone else's ticket
        await note(api, ws, mine, "VIP")  # internal note on my ticket
        await api.patch(  # staff-only change on my ticket (priority isn't shown to them)
            f"{API}/tickets/{mine['id']}",
            json={"priority": "urgent"},
            headers=in_org(ws.agent, ws.org_id),
        )
        assert await socket.nothing_received()

        await reply(api, ws, mine, ws.agent)
        assert await socket.receive_json() == {"type": "message.created", "ticket_id": mine["id"]}
        # ...and their own notification about it ("New reply on your request").
        assert await socket.receive_json() == {
            "type": "notification.created",
            "ticket_id": mine["id"],
        }
        # Assigning an open ticket also changes its status, which they do see.
        await api.patch(
            f"{API}/tickets/{mine['id']}",
            json={"assignee_id": ws.agent.id},
            headers=in_org(ws.agent, ws.org_id),
        )
        assert await socket.receive_json() == {"type": "ticket.updated", "ticket_id": mine["id"]}


async def test_ticket_stream_carries_only_that_ticket(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    watched = await create_ticket(api, ws.customer, ws.org_id)
    other = await create_ticket(api, ws.customer, ws.org_id)

    async with stream(ws.customer, ws.org_id, watched["id"]) as socket:
        await reply(api, ws, other, ws.agent)
        await reply(api, ws, watched, ws.agent)

        assert await socket.receive_json() == {
            "type": "message.created",
            "ticket_id": watched["id"],
        }
        assert await socket.nothing_received()


async def test_events_never_cross_organizations(api: httpx2.AsyncClient, ws: Workspace) -> None:
    stranger = await create_user(api)
    stranger_org = await create_org(api, stranger, "Stranger Co")
    stranger_customer = await create_customer(api, stranger, stranger_org["id"])

    async with stream(ws.admin, ws.org_id) as socket:
        await create_ticket(api, stranger, stranger_org["id"], customer_id=stranger_customer["id"])

        assert await socket.nothing_received()


# --- Who may subscribe -------------------------------------------------------------------------


async def connect_and_close(path: str, message: object) -> int:
    async with WebSocketSession(app, path) as socket:
        await socket.send_json(message)
        return (await socket.expect_close()).code


async def test_invalid_token_is_rejected(ws: Workspace) -> None:
    code = await connect_and_close(
        STREAM, {"type": "auth", "token": "not-a-jwt", "organization_id": ws.org_id}
    )

    assert code == 4401


async def test_malformed_auth_message_is_rejected(ws: Workspace) -> None:
    assert await connect_and_close(STREAM, {"hello": "there"}) == 4400


async def test_non_members_and_other_customers_tickets_are_not_found(
    api: httpx2.AsyncClient, ws: Workspace, outbox: Outbox
) -> None:
    outsider = await create_user(api)
    other = await create_user(api)
    await add_member(api, outbox, ws.admin, ws.org_id, other, "customer")
    theirs = await create_ticket(api, other, ws.org_id)

    not_member = await connect_and_close(
        STREAM, {"type": "auth", "token": token_of(outsider), "organization_id": ws.org_id}
    )
    not_mine = await connect_and_close(
        f"{STREAM}/{theirs['id']}",
        {"type": "auth", "token": token_of(ws.customer), "organization_id": ws.org_id},
    )

    assert not_member == not_mine == 4404


async def test_silent_clients_are_dropped(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime, "AUTH_TIMEOUT_SECONDS", 0.1)

    async with WebSocketSession(app, STREAM) as socket:
        closed = await socket.expect_close()

    assert closed.code == 4401


async def test_socket_closes_when_the_access_token_expires(ws: Workspace) -> None:
    now = datetime.now(UTC)
    short_lived = jwt.encode(
        {"sub": ws.agent.id, "type": "access", "iat": now, "exp": now + timedelta(seconds=1)},
        settings.jwt_secret_key.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )

    async with stream(None, ws.org_id, token=short_lived) as socket:
        closed = await socket.expect_close(wait_seconds=5)

    assert closed.code == 4401
    assert closed.reason == "Access token expired"


# --- Brokers -----------------------------------------------------------------------------------


def sample_event(org_id: uuid.UUID) -> TicketEvent:
    return TicketEvent.build(
        "message.created",
        organization_id=org_id,
        ticket_id=uuid.uuid4(),
        customer_user_id=None,
        staff_only=True,
    )


async def test_in_memory_broker_delivers_per_organization() -> None:
    broker = InMemoryEventBroker()
    org, other_org = uuid.uuid4(), uuid.uuid4()

    async with broker.subscribe(org) as events:
        await broker.publish(sample_event(other_org))
        await broker.publish(event := sample_event(org))
        received = await asyncio.wait_for(anext(events), 1)

    assert received == event


async def test_redis_broker_round_trip() -> None:
    client = Redis.from_url("redis://127.0.0.1:6379/15")
    broker = RedisEventBroker(client)
    org = uuid.uuid4()
    try:
        async with broker.subscribe(org) as events:
            await asyncio.sleep(0.05)  # let SUBSCRIBE reach Redis
            await broker.publish(event := sample_event(org))
            received = await asyncio.wait_for(anext(events), 2)
    finally:
        await client.aclose()

    assert received == event


async def test_redis_broker_publish_fails_open() -> None:
    client = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        await RedisEventBroker(client).publish(sample_event(uuid.uuid4()))  # no exception
    finally:
        await client.aclose()
