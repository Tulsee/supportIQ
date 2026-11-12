"""AI on a ticket (staff): a suggested reply and a summary of the conversation.

Suggestions go through the evidence-assessed answer pipeline and are written as a
reply to the customer; agents review and send them (nothing is sent
automatically). When no supported reply can be drafted, the ticket's timeline
records a staff-only "AI escalated" event.
"""

import uuid

from fastapi import APIRouter

from app.api.deps import (
    CurrentUser,
    DbSession,
    EmbedderDep,
    LLMClientDep,
    NliModelDep,
    NotificationsDep,
    RateLimiterDep,
    RerankerDep,
    TenantMembership,
    VectorStoreDep,
)
from app.repositories.retrieval import RunRepository
from app.schemas.ai_runs import AIRunRead, TicketAIRead
from app.schemas.common import error_responses
from app.services.retrieval import RetrievalService
from app.services.ticket_ai import TicketAIService

router = APIRouter(
    prefix="/tickets/{ticket_id}/ai",
    tags=["ticket AI"],
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
)


@router.post("/suggestion", summary="Draft a reply to the customer")
async def suggest_reply(
    ticket_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    embedder: EmbedderDep,
    vector_store: VectorStoreDep,
    reranker: RerankerDep,
    llm: LLMClientDep,
    nli: NliModelDep,
    limiter: RateLimiterDep,
    notifications: NotificationsDep,
) -> AIRunRead:
    """Answers the customer's latest message from the knowledge base, as a draft
    reply with citations, or explains why it can't (`answer.status`: clarify,
    escalated, abstained). A local model can take a minute or more."""
    retrieval = RetrievalService(
        session, embedder=embedder, vector_store=vector_store, reranker=reranker
    )
    service = TicketAIService(
        session,
        llm=llm,
        retrieval=retrieval,
        nli=nli,
        rate_limiter=limiter,
        notifications=notifications,
    )
    run, results = await service.suggest_reply(member, user, ticket_id)
    return AIRunRead.build_with(run, results)


@router.post("/summary", summary="Summarize the conversation")
async def summarize(
    ticket_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    llm: LLMClientDep,
    limiter: RateLimiterDep,
) -> AIRunRead:
    """3-5 bullet points for agents: the request, what was tried, what's open, the
    next step. Internal notes are included."""
    run = await TicketAIService(session, llm=llm, rate_limiter=limiter).summarize(
        member, user, ticket_id
    )
    return AIRunRead.build_with(run, [])


@router.get("", summary="The newest suggestion and summary")
async def latest(
    ticket_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> TicketAIRead:
    suggestion, summary = await TicketAIService(session).latest(member, ticket_id)
    runs = RunRepository(session)
    return TicketAIRead(
        suggestion=AIRunRead.build_with(suggestion, await runs.results(suggestion.id))
        if suggestion
        else None,
        summary=AIRunRead.build_with(summary, []) if summary else None,
    )
