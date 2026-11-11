"""Real-time ticket events: publish after a change, fan out to connected clients.

Events are *notifications*, not data: they say which ticket changed, never what
it says. Clients refetch through the REST API, which applies every authorization
rule, so a socket can never leak more than an API call would. The ``staff_only``
and ``customer_user_id`` fields decide who is notified at all (an internal note
must not even signal its existence to the customer).

Redis pub/sub carries events between API processes, one channel per organization.
Delivery is best effort: publishing never fails the request that made the
change, and clients also refetch when a view opens or regains focus.
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

EventType = Literal["ticket.created", "ticket.updated", "message.created"]
CHANNEL_PREFIX = "supportiq:tickets:"


@dataclass(frozen=True)
class TicketEvent:
    type: EventType
    organization_id: str
    ticket_id: str
    # The customer account the ticket belongs to, if any: who else may be notified.
    customer_user_id: str | None
    # Internal notes and staff-only changes: never signalled to customers.
    staff_only: bool = False

    @classmethod
    def build(
        cls,
        type_: EventType,
        *,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        customer_user_id: uuid.UUID | None,
        staff_only: bool = False,
    ) -> "TicketEvent":
        return cls(
            type=type_,
            organization_id=str(organization_id),
            ticket_id=str(ticket_id),
            customer_user_id=str(customer_user_id) if customer_user_id else None,
            staff_only=staff_only,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> "TicketEvent":
        return cls(**json.loads(raw))

    def public(self) -> dict[str, str]:
        """What a client receives: enough to know what to refetch."""
        return {"type": self.type, "ticket_id": self.ticket_id}


class EventBroker(Protocol):
    async def publish(self, event: TicketEvent) -> None: ...

    def subscribe(
        self, organization_id: uuid.UUID
    ) -> AbstractAsyncContextManager[AsyncIterator[TicketEvent]]:
        """Events for one organization, from now until the context exits."""
        ...


def _channel(organization_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{organization_id}"


class RedisEventBroker:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def publish(self, event: TicketEvent) -> None:
        try:
            await self.redis.publish(_channel(event.organization_id), event.to_json())
        except (RedisError, OSError) as exc:  # best effort: the change is committed
            logger.warning("Could not publish %s: %s", event.type, exc)

    @asynccontextmanager
    async def subscribe(
        self, organization_id: uuid.UUID
    ) -> AsyncIterator[AsyncIterator[TicketEvent]]:
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(_channel(organization_id))

        async def events() -> AsyncIterator[TicketEvent]:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield TicketEvent.from_json(message["data"])
                except (TypeError, ValueError, KeyError):
                    logger.warning("Ignoring malformed realtime event")

        try:
            yield events()
        finally:
            await pubsub.aclose()


class InMemoryEventBroker:
    """Single-process broker for tests (and a reference for the semantics)."""

    def __init__(self) -> None:
        self.published: list[TicketEvent] = []
        self._queues: dict[str, set[asyncio.Queue[TicketEvent]]] = defaultdict(set)

    async def publish(self, event: TicketEvent) -> None:
        self.published.append(event)
        for queue in list(self._queues[event.organization_id]):
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(
        self, organization_id: uuid.UUID
    ) -> AsyncIterator[AsyncIterator[TicketEvent]]:
        queue: asyncio.Queue[TicketEvent] = asyncio.Queue()
        self._queues[str(organization_id)].add(queue)

        async def events() -> AsyncIterator[TicketEvent]:
            while True:
                yield await queue.get()

        try:
            yield events()
        finally:
            self._queues[str(organization_id)].discard(queue)
