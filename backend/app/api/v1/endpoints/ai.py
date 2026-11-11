"""AI engine endpoints for the organization in ``X-Organization-Id`` (staff).

Evidence retrieval and recorded runs. Every retrieval is recorded with its
configuration and the score of every candidate at every stage, for inspection
and for comparing strategies.
"""

import uuid

from fastapi import APIRouter

from app.api.deps import (
    CurrentUser,
    DbSession,
    EmbedderDep,
    Pagination,
    RateLimiterDep,
    RerankerDep,
    TenantMembership,
    VectorStoreDep,
)
from app.schemas.common import Page, error_responses
from app.schemas.retrieval import RetrievalRunRead, RetrievalRunSummary, RetrieveRequest
from app.services.retrieval import RetrievalService

router = APIRouter(
    prefix="/ai", tags=["ai"], responses=error_responses(401, 403, 404, 422, 429, 503)
)


@router.post("/retrieve", summary="Retrieve evidence passages for a query")
async def retrieve(
    body: RetrieveRequest,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    embedder: EmbedderDep,
    vector_store: VectorStoreDep,
    reranker: RerankerDep,
    limiter: RateLimiterDep,
) -> RetrievalRunRead:
    """Keyword (BM25) and/or semantic search, fused with Reciprocal Rank Fusion and
    reranked by a cross-encoder. `strategy: auto` adapts to the query; the run is
    recorded (see `GET /ai/runs/{run_id}`)."""
    service = RetrievalService(
        session,
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
        rate_limiter=limiter,
    )
    run, results = await service.retrieve(member, user, body)
    return RetrievalRunRead.build_with(run, results)


@router.get("/runs", summary="Recent AI runs")
async def list_runs(
    member: TenantMembership, session: DbSession, page: Pagination
) -> Page[RetrievalRunSummary]:
    runs, total = await RetrievalService(session).list_runs(
        member, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[RetrievalRunSummary.build(run) for run in runs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runs/{run_id}", summary="Inspect an AI run")
async def get_run(
    run_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> RetrievalRunRead:
    run, results = await RetrievalService(session).get_run(member, run_id)
    return RetrievalRunRead.build_with(run, results)
