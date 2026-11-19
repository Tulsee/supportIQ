"""Knowledge gaps to articles (Module 13), in the organization in ``X-Organization-Id``.
Staff can read; admins analyze, draft, publish, recheck and review."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    GapQueueDep,
    IngestionQueueDep,
    Pagination,
    StorageDep,
    TenantMembership,
)
from app.core.config import settings
from app.models import GapJobKind, GapStatus
from app.schemas.common import Page, error_responses
from app.schemas.knowledge_gaps import (
    GapJobRead,
    GapPublish,
    GapRead,
    GapReview,
    GapSummaryRead,
)
from app.services.knowledge_gaps import (
    get_gap,
    list_gaps,
    publish_gap,
    request_job,
    review_gap,
    summary,
)

router = APIRouter(
    prefix="/knowledge/gaps",
    tags=["knowledge gaps"],
    responses=error_responses(401, 403, 404, 409, 422),
)


@router.get("/summary", summary="Gaps by status and the latest jobs")
async def gap_summary(member: TenantMembership, session: DbSession) -> GapSummaryRead:
    return GapSummaryRead.build(await summary(session, member), settings.gap_lookback_days)


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Group unanswered questions into gaps (admin)",
    responses=error_responses(503),
)
async def analyze_gaps(
    member: TenantMembership, user: CurrentUser, session: DbSession, queue: GapQueueDep
) -> GapJobRead:
    """Queues an analysis: questions not answered in the look-back window are grouped by
    meaning; new phrasings join existing gaps. No model call. 409 `job_running` if a
    gap job is running; follow it in the summary."""
    return GapJobRead.build(await request_job(session, member, user, queue, GapJobKind.ANALYZE))


@router.get("", summary="List gaps")
async def knowledge_gaps(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    status_filter: Annotated[
        GapStatus | None, Query(alias="status", description="Only gaps in this status.")
    ] = None,
) -> Page[GapRead]:
    """Most asked first, with their three most asked questions."""
    rows, total = await list_gaps(
        session, member, status=status_filter, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[GapRead.build(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/{gap_id}", summary="Get a gap with all its questions")
async def knowledge_gap(gap_id: uuid.UUID, member: TenantMembership, session: DbSession) -> GapRead:
    return GapRead.build(await get_gap(session, member, gap_id))


@router.post(
    "/{gap_id}/drafts",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Draft an article for a gap (admin)",
    responses=error_responses(503),
)
async def draft_gap(
    gap_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    queue: GapQueueDep,
) -> GapJobRead:
    """Queues a draft: resolved tickets that answered the gap's questions are found, and
    the model writes an article from the agents' replies; every statement is checked
    against the reply it quotes, for customer details, and against the knowledge base.
    Replaces the previous draft. 409 `gap_not_open` or `job_running`."""
    job = await request_job(session, member, user, queue, GapJobKind.DRAFT, gap_id)
    return GapJobRead.build(job)


@router.post(
    "/{gap_id}/publish",
    summary="Publish the article as a document (admin)",
    responses=error_responses(429, 503),
)
async def publish_knowledge_gap(
    gap_id: uuid.UUID,
    body: GapPublish,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    storage: StorageDep,
    queue: IngestionQueueDep,
) -> GapRead:
    """Creates a Markdown document from the (edited) article and queues its indexing;
    the gap becomes `published`. Recorded in the audit log. 409 `gap_not_open`, or
    `duplicate` if the same document exists."""
    row = await publish_gap(
        session,
        member,
        user,
        gap_id,
        title=body.title,
        body=body.body,
        visibility=body.visibility,
        storage=storage,
        queue=queue,
    )
    return GapRead.build(row)


@router.post(
    "/{gap_id}/rechecks",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask a published gap's questions again (admin)",
    responses=error_responses(503),
)
async def recheck_knowledge_gap(
    gap_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    queue: GapQueueDep,
) -> GapJobRead:
    """Queues a recheck: the most asked questions go through the answer pipeline again,
    to see how many are answered now and from the article. 409 `not_published`,
    `article_not_indexed` or `job_running`."""
    job = await request_job(session, member, user, queue, GapJobKind.RECHECK, gap_id)
    return GapJobRead.build(job)


@router.patch("/{gap_id}", summary="Dismiss or reopen a gap (admin)")
async def review_knowledge_gap(
    gap_id: uuid.UUID,
    body: GapReview,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
) -> GapRead:
    """Recorded in the audit log. A published gap can't be reopened or dismissed:
    edit or delete its document instead."""
    return GapRead.build(await review_gap(session, member, user, gap_id, body.status, body.note))
