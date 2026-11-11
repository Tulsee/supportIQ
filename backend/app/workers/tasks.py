"""Background tasks."""

import asyncio
import uuid

from celery import Task

from app.services.ingestion import TransientIngestionError, run_ingestion_job
from app.workers.celery_app import celery_app

MAX_RETRIES = 3


@celery_app.task(
    bind=True,
    name="documents.ingest",
    autoretry_for=(TransientIngestionError,),
    max_retries=MAX_RETRIES,
    retry_backoff=10,  # 10 s, 20 s, 40 s (with jitter)
    retry_backoff_max=300,
    retry_jitter=True,
)
def ingest_document(self: Task, document_id: str, ingestion_id: str) -> str:
    final_attempt = self.request.retries >= MAX_RETRIES
    outcome = asyncio.run(
        run_ingestion_job(
            uuid.UUID(document_id), uuid.UUID(ingestion_id), final_attempt=final_attempt
        )
    )
    return outcome.value
