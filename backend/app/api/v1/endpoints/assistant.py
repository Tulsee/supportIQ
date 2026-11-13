"""The customer assistant (Module 12), for customer members of the organization in
``X-Organization-Id``, when the organization has turned it on (``self_service``).

Customers ask questions answered from the organization's public documents only,
rate answers, and hand a conversation over to the support team as a request. Staff
read the conversation a request came from on the ticket.
"""

import uuid

from fastapi import APIRouter

from app.api.deps import (
    CurrentUser,
    DbSession,
    EmbedderDep,
    EventBrokerDep,
    LLMClientDep,
    LocalLLMClientDep,
    NliModelDep,
    NotificationsDep,
    Pagination,
    RateLimiterDep,
    RerankerDep,
    RoutingQueueDep,
    TenantMembership,
    TriageQueueDep,
    VectorStoreDep,
)
from app.schemas.assistant import (
    AssistantConversationRead,
    AssistantConversationSummary,
    AssistantQuestion,
    FeedbackRequest,
    HandoffRequest,
    StaffAssistantConversationRead,
)
from app.schemas.common import Page, error_responses
from app.services.assistant import AssistantService
from app.services.retrieval import RetrievalService
from app.services.tickets import TicketService
from app.services.triage import after_ticket_opened

router = APIRouter(
    prefix="/assistant",
    tags=["customer assistant"],
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
)
ticket_router = APIRouter(
    prefix="/tickets", tags=["customer assistant"], responses=error_responses(401, 403, 422)
)


@router.post("/questions", summary="Ask the assistant (customers)")
async def ask(
    body: AssistantQuestion,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    embedder: EmbedderDep,
    vector_store: VectorStoreDep,
    reranker: RerankerDep,
    llm: LLMClientDep,
    local_llm: LocalLLMClientDep,
    nli: NliModelDep,
    limiter: RateLimiterDep,
) -> AssistantConversationRead:
    """Starts a conversation, or continues an open one (`conversation_id`). The reply's
    `outcome` is `answered` (with sources), `clarify`, or `unable`: the assistant
    answers only when public documents fully support the answer, and otherwise offers
    a person (`POST /assistant/conversations/{id}/handoff`). 403 `self_service_off`
    when the organization hasn't turned the assistant on; 409 `conversation_closed`
    for a resolved or handed-off conversation. A local model can take a minute."""
    retrieval = RetrievalService(
        session, embedder=embedder, vector_store=vector_store, reranker=reranker
    )
    service = AssistantService(
        session, retrieval=retrieval, llm=llm, local_llm=local_llm, nli=nli, rate_limiter=limiter
    )
    return AssistantConversationRead.build(await service.ask(member, user, body))


@router.get("/conversations", summary="Your conversations with the assistant")
async def list_conversations(
    member: TenantMembership, user: CurrentUser, session: DbSession, page: Pagination
) -> Page[AssistantConversationSummary]:
    conversations, total = await AssistantService(session).list_conversations(
        member, user, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[AssistantConversationSummary.build(c) for c in conversations],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/conversations/{conversation_id}", summary="A conversation with the assistant")
async def get_conversation(
    conversation_id: uuid.UUID, member: TenantMembership, user: CurrentUser, session: DbSession
) -> AssistantConversationRead:
    conversation = await AssistantService(session).get_conversation(member, user, conversation_id)
    return AssistantConversationRead.build(conversation)


@router.post("/conversations/{conversation_id}/feedback", summary="Did this answer your question?")
async def rate_answer(
    conversation_id: uuid.UUID,
    body: FeedbackRequest,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
) -> AssistantConversationRead:
    """`helpful: true` resolves the conversation; `false` leaves it open (offer a
    person). Rating again replaces the rating. Only `answered` replies can be rated."""
    conversation = await AssistantService(session).rate(member, user, conversation_id, body)
    return AssistantConversationRead.build(conversation)


@router.post("/conversations/{conversation_id}/handoff", summary="Send the conversation to support")
async def hand_off(
    conversation_id: uuid.UUID,
    body: HandoffRequest,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    events: EventBrokerDep,
    notifications: NotificationsDep,
    routing: RoutingQueueDep,
    triage: TriageQueueDep,
) -> AssistantConversationRead:
    """Opens a request with the customer's questions (and `note`) as its first message;
    SLA clocks, triage, routing and notifications apply as for any request. Staff see
    the whole conversation on the ticket. 409 `already_handed_off` the second time."""
    tickets = TicketService(session, events, notifications)
    conversation, ticket = await AssistantService(session).hand_off(
        member, user, conversation_id, body.note, tickets
    )
    await after_ticket_opened(triage, routing, member, ticket)
    return AssistantConversationRead.build(conversation)


@ticket_router.get(
    "/{ticket_id}/self-service", summary="The assistant conversation a request came from (staff)"
)
async def ticket_conversation(
    ticket_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> StaffAssistantConversationRead | None:
    """`null` when the request wasn't opened from a conversation. Each reply includes
    its AI run (open it in the AI inspector) and why the assistant answered or not."""
    return await AssistantService(session).for_ticket(member, ticket_id)
