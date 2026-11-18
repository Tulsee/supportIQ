"""Knowledge-base contradictions (Module 13), in the organization in
``X-Organization-Id``. Staff can read; admins scan and review."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ContradictionQueueDep,
    CurrentUser,
    DbSession,
    Pagination,
    TenantMembership,
)
from app.models import FindingStatus
from app.schemas.common import Page, error_responses
from app.schemas.contradictions import ContradictionSummary, FindingRead, FindingReview, ScanRead
from app.services.contradictions import (
    get_finding,
    list_findings,
    request_scan,
    review_finding,
    summary,
)

router = APIRouter(
    prefix="/knowledge/contradictions",
    tags=["contradictions"],
    responses=error_responses(401, 403, 404, 409, 422),
)


@router.get("/summary", summary="Findings by status and the latest scan")
async def contradiction_summary(
    member: TenantMembership, session: DbSession
) -> ContradictionSummary:
    return ContradictionSummary.build(await summary(session, member))


@router.post(
    "/scans",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Scan the knowledge base now (admin)",
    responses=error_responses(503),
)
async def start_contradiction_scan(
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    queue: ContradictionQueueDep,
) -> ScanRead:
    """Queues a scan: similar passages from different documents are compared by the
    model; only pairs not checked before (new or changed text) are sent, up to the
    per-scan limit. 409 `scan_running` if one is running; follow it in the summary."""
    return ScanRead.build(await request_scan(session, member, user, queue))


@router.get("", summary="List findings")
async def contradiction_findings(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    status_filter: Annotated[
        FindingStatus | None, Query(alias="status", description="Only findings in this status.")
    ] = None,
    document_id: Annotated[
        uuid.UUID | None, Query(description="Only findings involving this document.")
    ] = None,
) -> Page[FindingRead]:
    """Pairs of passages the model found to conflict, with the quotes from each side,
    newest first."""
    rows, total = await list_findings(
        session,
        member,
        status=status_filter,
        document_id=document_id,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[FindingRead.build(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{finding_id}", summary="Get a finding")
async def contradiction_finding(
    finding_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> FindingRead:
    return FindingRead.build(await get_finding(session, member, finding_id))


@router.patch("/{finding_id}", summary="Dismiss, resolve or reopen a finding (admin)")
async def review_contradiction(
    finding_id: uuid.UUID,
    body: FindingReview,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
) -> FindingRead:
    """Recorded in the audit log. A dismissed finding isn't reported again unless one of
    its passages changes."""
    row = await review_finding(session, member, user, finding_id, body.status, body.note)
    return FindingRead.build(row)
