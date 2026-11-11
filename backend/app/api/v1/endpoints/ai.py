"""AI engine endpoints for the organization in ``X-Organization-Id`` (staff).

Evidence retrieval, answers from the knowledge base, and recorded runs. Every
search and every answer is recorded with its configuration, the score of every
candidate at every stage, and (for answers) the exact prompt and model settings,
for inspection and for comparing strategies and models.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import (
    CurrentUser,
    DbSession,
    EmbedderDep,
    LLMClientDep,
    Pagination,
    RateLimiterDep,
    RerankerDep,
    TenantMembership,
    VectorStoreDep,
)
from app.models import AIRunKind
from app.schemas.ai_runs import AIRunRead, AIRunSummary
from app.schemas.answers import AskRequest, ModelStatusRead
from app.schemas.common import Page, error_responses
from app.schemas.retrieval import RetrieveRequest
from app.services.answering import AnswerService
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
) -> AIRunRead:
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
    return AIRunRead.build_with(run, results)


@router.post("/chat", summary="Answer a question from the knowledge base")
async def chat(
    body: AskRequest,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    embedder: EmbedderDep,
    vector_store: VectorStoreDep,
    reranker: RerankerDep,
    llm: LLMClientDep,
    limiter: RateLimiterDep,
) -> AIRunRead:
    """Retrieves passages as `POST /ai/retrieve` does, then has the language model
    answer from those passages only, citing them by number. Citations always resolve
    to retrieved passages (document, version, page, section); numbers the model
    invents are removed. If the model is unavailable the run is still recorded,
    with `answer.status = failed` and the reason in `answer.error`. A local model
    can take a minute or more."""
    retrieval = RetrievalService(
        session, embedder=embedder, vector_store=vector_store, reranker=reranker
    )
    service = AnswerService(session, retrieval=retrieval, llm=llm, rate_limiter=limiter)
    run, results = await service.answer(member, user, body)
    return AIRunRead.build_with(run, results)


@router.get("/model", summary="The answer model and whether it is ready")
async def model_status(
    member: TenantMembership, session: DbSession, llm: LLMClientDep
) -> ModelStatusRead:
    """Checks that the model can be reached (it is never run). For Claude, only
    that an API key is configured; the key itself is checked by the first question."""
    retrieval = RetrievalService(session)
    status = await AnswerService(session, retrieval=retrieval, llm=llm).model_status(member)
    return ModelStatusRead.build(llm.provider, llm.model, status)


@router.get("/runs", summary="Recent AI runs")
async def list_runs(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    kind: Annotated[AIRunKind | None, Query(description="Only searches, or only answers.")] = None,
) -> Page[AIRunSummary]:
    runs, total = await RetrievalService(session).list_runs(
        member, kind=kind, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AIRunSummary.build(run) for run in runs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runs/{run_id}", summary="Inspect an AI run")
async def get_run(run_id: uuid.UUID, member: TenantMembership, session: DbSession) -> AIRunRead:
    run, results = await RetrievalService(session).get_run(member, run_id)
    return AIRunRead.build_with(run, results)
