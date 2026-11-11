"""Celery application for background jobs.

Run a worker (from backend/):

    uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo

``--pool=solo`` is required on Windows; on Linux the default prefork pool works
(each process loads its own copy of the embedding model).
"""

from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(settings.log_level)

celery_app = Celery("supportiq", broker=settings.celery_broker_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_default_queue="supportiq",
    task_ignore_result=True,  # outcomes are recorded on the document, not in a backend
    # Acknowledge after the task finishes: a crashed worker's job is redelivered.
    # Ingestion is idempotent per ingestion_id, so a second delivery is harmless.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # long jobs: don't reserve work another worker could do
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 60 * 60},
    task_serializer="json",
    accept_content=["json"],
)
