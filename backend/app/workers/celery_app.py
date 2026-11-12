"""Celery application for background jobs.

Run a worker (from backend/):

    uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo

``--pool=solo`` is required on Windows; on Linux the default prefork pool works
(each process loads its own copy of the embedding model).
"""

from typing import Any

from celery import Celery
from celery.signals import worker_ready, worker_shutdown
from redis import Redis

from app.core.config import settings
from app.core.logging import configure_logging
from app.workers.heartbeat import Heartbeat

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

_heartbeat: Heartbeat | None = None


# Periodic jobs, run by the scheduler (Celery beat), executed by the workers:
#   uv run celery -A app.workers.celery_app beat --loglevel=info
# Each job is safe to run twice at once (row locks with SKIP LOCKED).
celery_app.conf.beat_schedule = {
    name: job
    for name, job, enabled in (
        (
            "auto-close-resolved-tickets",
            {"task": "tickets.auto_close", "schedule": settings.auto_close_interval_minutes * 60},
            settings.auto_close_interval_minutes > 0,
        ),
        (
            "sla-monitor",
            # Expire quickly: a backlog of minute-ly checks is pointless.
            {
                "task": "sla.evaluate",
                "schedule": settings.sla_monitor_interval_seconds,
                "options": {"expires": max(settings.sla_monitor_interval_seconds, 30)},
            },
            settings.sla_monitor_interval_seconds > 0,
        ),
        (
            "routing-sweep",
            {
                "task": "routing.sweep",
                "schedule": settings.routing_sweep_interval_seconds,
                "options": {"expires": max(settings.routing_sweep_interval_seconds, 30)},
            },
            settings.routing_sweep_interval_seconds > 0,
        ),
    )
    if enabled
}


@worker_ready.connect
def _announce_worker(sender: Any = None, **_: Any) -> None:
    """Let the app know a worker is running (see app.workers.heartbeat)."""
    global _heartbeat
    node = getattr(sender, "hostname", None) or "worker"
    _heartbeat = Heartbeat(Redis.from_url(settings.redis_url), node)
    _heartbeat.start()


@worker_shutdown.connect
def _withdraw_worker(**_: Any) -> None:
    if _heartbeat is not None:
        _heartbeat.stop()
