"""Handing jobs (document ingestion, notification emails, ticket routing) to the
background workers."""

import uuid
from collections.abc import Sequence
from typing import Protocol

from anyio import to_thread


class QueueUnavailableError(Exception):
    """The job broker can't be reached; the job was not queued."""


class IngestionQueue(Protocol):
    async def enqueue(self, document_id: uuid.UUID, ingestion_id: uuid.UUID) -> None: ...


class CeleryIngestionQueue:
    async def enqueue(self, document_id: uuid.UUID, ingestion_id: uuid.UUID) -> None:
        from kombu.exceptions import OperationalError

        from app.workers.tasks import ingest_document

        def _send() -> None:
            ingest_document.apply_async(
                args=[str(document_id), str(ingestion_id)],
                # Fail fast instead of hanging the request when Redis is down.
                retry=True,
                retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
            )

        try:
            await to_thread.run_sync(_send)
        except (OperationalError, OSError) as exc:
            raise QueueUnavailableError(str(exc)) from exc


class NotificationMailer(Protocol):
    async def enqueue(self, notification_ids: Sequence[uuid.UUID]) -> None: ...


class CeleryNotificationMailer:
    async def enqueue(self, notification_ids: Sequence[uuid.UUID]) -> None:
        from kombu.exceptions import OperationalError

        from app.workers.tasks import email_notifications

        def _send() -> None:
            email_notifications.apply_async(
                args=[[str(i) for i in notification_ids]],
                retry=True,
                retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
            )

        try:
            await to_thread.run_sync(_send)
        except (OperationalError, OSError) as exc:
            raise QueueUnavailableError(str(exc)) from exc


class RoutingQueue(Protocol):
    async def enqueue(self, ticket_id: uuid.UUID) -> None: ...


class CeleryRoutingQueue:
    async def enqueue(self, ticket_id: uuid.UUID) -> None:
        from kombu.exceptions import OperationalError

        from app.workers.tasks import route_ticket

        def _send() -> None:
            route_ticket.apply_async(
                args=[str(ticket_id)],
                retry=True,
                retry_policy={"max_retries": 2, "interval_start": 0.2, "interval_step": 0.5},
            )

        try:
            await to_thread.run_sync(_send)
        except (OperationalError, OSError) as exc:
            raise QueueUnavailableError(str(exc)) from exc
