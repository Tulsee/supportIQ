"""Background tasks."""

import asyncio
import uuid

from celery import Task

from app.services.automation import run_auto_close_job
from app.services.ingestion import TransientIngestionError, run_ingestion_job
from app.services.notifications import run_email_job
from app.services.routing import run_routing_job, run_routing_sweep
from app.services.sla_monitor import run_sla_job
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


@celery_app.task(
    name="notifications.email",
    # SMTP and network failures: retry with backoff. Notifications already sent are
    # marked, so a retry only sends the rest.
    autoretry_for=(OSError, ConnectionError, TimeoutError),
    max_retries=5,
    retry_backoff=30,
    retry_backoff_max=900,
    retry_jitter=True,
)
def email_notifications(notification_ids: list[str]) -> int:
    return asyncio.run(run_email_job([uuid.UUID(i) for i in notification_ids]))


@celery_app.task(name="tickets.auto_close", ignore_result=True)
def auto_close_tickets() -> int:
    """Close resolved tickets past their organization's auto-close window."""
    return asyncio.run(run_auto_close_job())


@celery_app.task(name="sla.evaluate", ignore_result=True)
def evaluate_sla() -> dict[str, int]:
    """Record SLA clocks that became due soon or breached, and notify."""
    result = asyncio.run(run_sla_job())
    return {"at_risk": result.at_risk, "breached": result.breached}


@celery_app.task(
    name="routing.route_ticket",
    ignore_result=True,
    # Database or Redis hiccups: retry. Routing is idempotent per ticket (at most one
    # automatic decision), and the sweep catches anything that still slips through.
    autoretry_for=(OSError, ConnectionError, TimeoutError),
    max_retries=3,
    retry_backoff=5,
    retry_jitter=True,
)
def route_ticket(ticket_id: str) -> str | None:
    """Route a newly opened ticket (organizations with routing on or in shadow mode)."""
    return asyncio.run(run_routing_job(uuid.UUID(ticket_id)))


@celery_app.task(name="routing.sweep", ignore_result=True)
def sweep_unrouted_tickets() -> int:
    """Route open tickets whose routing job never ran."""
    return asyncio.run(run_routing_sweep())
