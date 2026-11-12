"""Real-time ticket notifications over WebSocket.

    /api/v1/ws/tickets               every ticket the caller may see in an organization
    /api/v1/ws/tickets/{ticket_id}   one ticket

Protocol:

1. Connect, then send ``{"type": "auth", "token": "<access token>",
   "organization_id": "<uuid>"}`` within 10 seconds. (Browsers can't set headers
   on a WebSocket handshake, and URLs end up in logs, so the token travels in the
   first message rather than the query string.)
2. The server answers ``{"type": "ready"}``, then sends notifications such as
   ``{"type": "message.created", "ticket_id": "..."}``. Refetch the ticket through
   the REST API; notifications never carry content.
3. Close codes: 4400 malformed auth message; 4401 not authenticated, or the access
   token expired (reconnect with a fresh one); 4404 organization or ticket not
   found, or no access to it. Expiry also re-checks membership periodically.

Staff receive every event of the organization. Customers receive only events on
their own tickets, and never staff-only ones (internal notes, assignment changes).
``notification.created`` goes only to its recipient, on the organization-wide socket.

A staff member's open socket also keeps them "online" (see
``app/websocket/presence.py``): marked when it's ready, then every 30 seconds.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession, EventBrokerDep, PresenceDep
from app.core.database import set_rls_context
from app.core.security import InvalidTokenError, decode_access_claims, utcnow
from app.repositories.organizations import OrganizationRepository
from app.repositories.tickets import TicketRepository
from app.repositories.users import UserRepository
from app.services.authorization import STAFF_ROLES
from app.websocket import presence as presence_settings
from app.websocket.broker import EventBroker, TicketEvent
from app.websocket.presence import PresenceTracker

router = APIRouter(prefix="/ws", tags=["realtime"])

AUTH_TIMEOUT_SECONDS = 10.0
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404


class AuthMessage(BaseModel):
    type: Literal["auth"]
    token: str = Field(max_length=4096)
    organization_id: uuid.UUID


@dataclass(frozen=True)
class Subscriber:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    staff: bool
    ticket_id: uuid.UUID | None
    expires_at: datetime

    def wants(self, event: TicketEvent) -> bool:
        if event.recipient_user_id is not None:
            return self.ticket_id is None and event.recipient_user_id == str(self.user_id)
        if self.ticket_id is not None and event.ticket_id != str(self.ticket_id):
            return False
        if self.staff:
            return True
        return not event.staff_only and event.customer_user_id == str(self.user_id)


class _Rejected(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


async def _authenticate(
    websocket: WebSocket, session: AsyncSession, ticket_id: uuid.UUID | None
) -> Subscriber:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS)
    except TimeoutError:
        raise _Rejected(CLOSE_UNAUTHORIZED, "Authentication timed out") from None
    try:
        hello = AuthMessage.model_validate_json(raw)
    except ValidationError:
        raise _Rejected(CLOSE_BAD_REQUEST, "Expected an auth message") from None
    try:
        claims = decode_access_claims(hello.token)
    except InvalidTokenError:
        raise _Rejected(CLOSE_UNAUTHORIZED, "Invalid or expired access token") from None

    user = await UserRepository(session).get(claims.user_id)
    if user is None or not user.is_active:
        raise _Rejected(CLOSE_UNAUTHORIZED, "Invalid or expired access token")
    await set_rls_context(session, user_id=user.id)
    membership = await OrganizationRepository(session).get_membership(
        hello.organization_id, user.id
    )
    if membership is None:
        raise _Rejected(CLOSE_NOT_FOUND, "Organization not found")
    await set_rls_context(session, organization_id=hello.organization_id)
    staff = membership.role in STAFF_ROLES
    if ticket_id is not None:
        ticket = await TicketRepository(session).get(hello.organization_id, ticket_id)
        if ticket is None or (not staff and ticket.customer.user_id != user.id):
            raise _Rejected(CLOSE_NOT_FOUND, "Ticket not found")
    return Subscriber(user.id, hello.organization_id, staff, ticket_id, claims.expires_at)


async def _pump(
    websocket: WebSocket,
    events: AsyncIterator[TicketEvent],
    subscriber: Subscriber,
    presence: PresenceTracker,
) -> bool:
    """Forward events until the client leaves (False) or its token expires (True)."""

    async def stay_present() -> None:
        while True:
            await anyio.sleep(presence_settings.REFRESH_SECONDS)
            await presence.touch(subscriber.organization_id, subscriber.user_id)

    async def forward() -> None:
        try:
            async for event in events:
                if subscriber.wants(event):
                    await websocket.send_json(event.public())
        except (WebSocketDisconnect, RuntimeError, OSError):
            return  # the client went away mid-send

    async def until_client_leaves() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass  # clients have nothing more to say; ignore anything they send

    seconds_left = (subscriber.expires_at - utcnow()).total_seconds()
    with anyio.move_on_after(max(seconds_left, 0)) as deadline:
        async with anyio.create_task_group() as tasks:

            async def then_stop(task: Callable[[], Awaitable[None]]) -> None:
                await task()
                tasks.cancel_scope.cancel()

            tasks.start_soon(then_stop, forward)
            tasks.start_soon(then_stop, until_client_leaves)
            if subscriber.staff:
                tasks.start_soon(stay_present)
    return deadline.cancelled_caught


async def _serve(
    websocket: WebSocket,
    session: AsyncSession,
    broker: EventBroker,
    presence: PresenceTracker,
    ticket_id: uuid.UUID | None,
) -> None:
    await websocket.accept()
    try:
        try:
            subscriber = await _authenticate(websocket, session, ticket_id)
        finally:
            # Release the database connection: a socket may stay open for minutes.
            await session.rollback()
    except _Rejected as rejection:
        await websocket.close(code=rejection.code, reason=rejection.reason)
        return
    except WebSocketDisconnect:
        return

    # Subscribe before announcing "ready", so nothing published after it is missed.
    async with broker.subscribe(subscriber.organization_id) as events:
        if subscriber.staff:
            await presence.touch(subscriber.organization_id, subscriber.user_id)
        await websocket.send_json({"type": "ready"})
        expired = await _pump(websocket, events, subscriber, presence)
    if expired:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Access token expired")


@router.websocket("/tickets")
async def organization_tickets(
    websocket: WebSocket, session: DbSession, broker: EventBrokerDep, presence: PresenceDep
) -> None:
    await _serve(websocket, session, broker, presence, ticket_id=None)


@router.websocket("/tickets/{ticket_id}")
async def single_ticket(
    ticket_id: uuid.UUID,
    websocket: WebSocket,
    session: DbSession,
    broker: EventBrokerDep,
    presence: PresenceDep,
) -> None:
    await _serve(websocket, session, broker, presence, ticket_id=ticket_id)
