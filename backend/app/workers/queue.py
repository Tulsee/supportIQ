"""Handing ingestion jobs to the background workers."""

import uuid
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
