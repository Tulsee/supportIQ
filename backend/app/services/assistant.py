"""The customer assistant (Module 12): customers ask the portal assistant, rate its
answers, and hand a conversation over to a person as a request.

    question -> the answer pipeline with public documents only, the customer policy
                (assessed; a partial answer abstains) and the self-service prompt
             -> answered (with sources) / clarify / unable ("Contact support")

Customer messages are committed before the model is called (it can take a minute),
so a question is never lost. Follow-ups get the conversation's last turns, read from
the database: a client can't supply a fake assistant turn.
"""

import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ai_engine.evidence.nli import NliModel
from ai_engine.evidence.policy import Thresholds
from ai_engine.generation.llm import LLMClient
from app.core.config import settings
from app.core.database import rls_bypass
from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    TooManyRequestsError,
)
from app.core.knowledge import policy_thresholds
from app.core.rate_limit import RateLimiter
from app.core.security import utcnow
from app.models import (
    AIAnswer,
    AIRun,
    AIRunKind,
    AnswerStatus,
    AssistantConversation,
    AssistantMessage,
    AssistantOutcome,
    AssistantRating,
    AssistantRole,
    ConversationStatus,
    EvidenceAssessment,
    OrganizationMember,
    OrgRole,
    Ticket,
    TicketEvent,
    TicketEventType,
    User,
)
from app.schemas.answers import AskRequest, HistoryTurn
from app.schemas.assistant import (
    AssistantConversationSummary,
    AssistantQuestion,
    AssistantRunInfo,
    AssistantSource,
    FeedbackRequest,
    StaffAssistantConversationRead,
    StaffAssistantMessageRead,
)
from app.schemas.organizations import OrganizationSettings
from app.schemas.tickets import TicketCreate
from app.services.answering import AnswerService
from app.services.authorization import STAFF_ROLES, require_role
from app.services.customers import CustomerService
from app.services.retrieval import RetrievalService
from app.services.tickets import TicketService

UNABLE_REPLY = (
    "I can't answer this reliably from our help articles. Our support team can help: "
    "use Contact support to send them your question."
)
UNAVAILABLE_REPLY = (
    "The assistant isn't available right now. Use Contact support and our team will help."
)
TITLE_LENGTH = 120
HISTORY_TURNS = 3  # the most AskRequest accepts
MAX_MESSAGE = 20_000  # a ticket message's limit


def customer_thresholds() -> Thresholds:
    """The staff policy's tuned thresholds, but a partial answer always abstains: a
    customer can't tell which part is missing, and no agent reviews the reply."""
    return replace(policy_thresholds(), abstain_on_partial=True)


def short_title(question: str) -> str:
    text = " ".join(question.split())
    if len(text) <= TITLE_LENGTH:
        return text
    cut = text[: TITLE_LENGTH - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def self_service_settings(member: OrganizationMember) -> OrganizationSettings:
    return OrganizationSettings.model_validate(member.organization.settings or {})


def _sources(answer: AIAnswer) -> list[dict[str, Any]]:
    """One source per cited passage, in passage order, as the customer will see it."""
    seen: dict[int, dict[str, Any]] = {}
    for citation in answer.citations:
        if citation.passage_number in seen:
            continue
        document = citation.document
        seen[citation.passage_number] = AssistantSource(
            passage_number=citation.passage_number,
            document_id=citation.document_id,
            title=document.title if document is not None else "A help article",
            page_number=citation.page_number,
            page_end=citation.page_end,
            section_heading=citation.section_heading,
        ).model_dump(mode="json")
    return [seen[n] for n in sorted(seen)]


class AssistantService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        retrieval: RetrievalService | None = None,
        llm: LLMClient | None = None,
        local_llm: LLMClient | None = None,
        nli: NliModel | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.retrieval = retrieval
        self.llm = llm
        self.local_llm = local_llm
        self.nli = nli
        self.rate_limiter = rate_limiter

    # --- Asking ------------------------------------------------------------------------------

    async def ask(
        self, member: OrganizationMember, user: User, body: AssistantQuestion
    ) -> AssistantConversation:
        require_role(member, OrgRole.CUSTOMER)
        options = self_service_settings(member)
        if options.self_service != "on":
            raise PermissionDeniedError(
                "The assistant isn't available in this organization", code="self_service_off"
            )
        await self._throttle(user)

        if body.conversation_id is not None:
            conversation = await self._own(member, user, body.conversation_id, for_update=True)
            if conversation.status != ConversationStatus.OPEN:
                raise ConflictError(
                    "This conversation is closed. Start a new one to ask something else.",
                    code="conversation_closed",
                )
            history = await self._history(conversation.id)
        else:
            customer = await CustomerService(self.session).ensure_for_member(member, user)
            conversation = AssistantConversation(
                id=uuid.uuid4(),
                organization_id=member.organization_id,
                user_id=user.id,
                customer_id=customer.id,
                status=ConversationStatus.OPEN,
                title=short_title(body.question),
            )
            self.session.add(conversation)
            await self.session.flush()
            history = []
        self.session.add(
            AssistantMessage(
                organization_id=member.organization_id,
                conversation_id=conversation.id,
                role=AssistantRole.CUSTOMER,
                body=body.question,
                sources=[],
            )
        )
        conversation.updated_at = utcnow()
        await self.session.commit()  # the question is kept even if answering fails

        reply = await self._answer(member, user, conversation.id, body.question, history, options)
        self.session.add(reply)
        await self.session.commit()
        return await self._load(member.organization_id, conversation.id)

    async def _answer(
        self,
        member: OrganizationMember,
        user: User,
        conversation_id: uuid.UUID,
        question: str,
        history: list[HistoryTurn],
        options: OrganizationSettings,
    ) -> AssistantMessage:
        assert self.retrieval is not None and self.llm is not None
        llm = self.local_llm if options.self_service_model == "local" else self.llm
        assert llm is not None
        answers = AnswerService(
            self.session,
            retrieval=self.retrieval,
            llm=llm,
            nli=self.nli,
            policy="assessed",
            thresholds=customer_thresholds(),
        )
        reply = AssistantMessage(
            organization_id=member.organization_id,
            conversation_id=conversation_id,
            role=AssistantRole.ASSISTANT,
            sources=[],
        )
        try:
            run, _ = await answers.answer_question(
                member,
                user,
                AskRequest(query=question, history=history),
                kind=AIRunKind.SELF_SERVICE,
                audience="self_service",
                public_only=True,
            )
        except ServiceUnavailableError:  # search is down: say so, offer a person
            await self.session.rollback()
            reply.outcome = AssistantOutcome.UNABLE
            reply.body = UNAVAILABLE_REPLY
            return reply

        reply.ai_run_id = run.id
        answer = run.answer
        if answer is not None and answer.status == AnswerStatus.ANSWERED and answer.text:
            reply.outcome = AssistantOutcome.ANSWERED
            reply.body = answer.text
            reply.sources = _sources(answer)
        elif answer is not None and answer.status == AnswerStatus.CLARIFY and answer.text:
            reply.outcome = AssistantOutcome.CLARIFY
            reply.body = answer.text
        else:  # escalated, abstained, declined, no passages, uncited or failed
            reply.outcome = AssistantOutcome.UNABLE
            reply.body = UNABLE_REPLY
        return reply

    async def _history(self, conversation_id: uuid.UUID) -> list[HistoryTurn]:
        """The last question/reply pairs the assistant could use (answers and clarifying
        questions; "unable" replies carry nothing to follow up on)."""
        messages = (
            await self.session.scalars(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.seq)
            )
        ).all()
        turns: list[HistoryTurn] = []
        question: str | None = None
        for message in messages:
            if message.role == AssistantRole.CUSTOMER:
                question = message.body
            elif question is not None and message.outcome in (
                AssistantOutcome.ANSWERED,
                AssistantOutcome.CLARIFY,
            ):
                turns.append(HistoryTurn(question=question[:500], answer=message.body[:4000]))
                question = None
        return turns[-HISTORY_TURNS:]

    # --- Reading -----------------------------------------------------------------------------

    async def list_conversations(
        self, member: OrganizationMember, user: User, *, limit: int, offset: int
    ) -> tuple[list[AssistantConversation], int]:
        require_role(member, OrgRole.CUSTOMER)
        condition = (AssistantConversation.organization_id == member.organization_id) & (
            AssistantConversation.user_id == user.id
        )
        items = await self.session.scalars(
            select(AssistantConversation)
            .options(joinedload(AssistantConversation.ticket))
            .where(condition)
            .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id)
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(AssistantConversation).where(condition)
        )
        return list(items.all()), int(total or 0)

    async def get_conversation(
        self, member: OrganizationMember, user: User, conversation_id: uuid.UUID
    ) -> AssistantConversation:
        require_role(member, OrgRole.CUSTOMER)
        await self._own(member, user, conversation_id)
        return await self._load(member.organization_id, conversation_id)

    async def for_ticket(
        self, member: OrganizationMember, ticket_id: uuid.UUID
    ) -> StaffAssistantConversationRead | None:
        """The conversation a request was opened from, with why each reply was what it
        was (staff only); None if it wasn't opened from one."""
        require_role(member, *STAFF_ROLES)
        conversation = await self.session.scalar(
            select(AssistantConversation)
            .options(
                selectinload(AssistantConversation.messages),
                joinedload(AssistantConversation.ticket),
                joinedload(AssistantConversation.customer),
            )
            .where(
                AssistantConversation.organization_id == member.organization_id,
                AssistantConversation.ticket_id == ticket_id,
            )
            .order_by(AssistantConversation.handed_off_at.desc())
            .limit(1)
        )
        if conversation is None:
            return None
        run_ids = [m.ai_run_id for m in conversation.messages if m.ai_run_id is not None]
        details: dict[uuid.UUID, AssistantRunInfo] = {}
        if run_ids:
            rows = await self.session.execute(
                select(AIRun.id, AIAnswer.status, EvidenceAssessment.reason)
                .outerjoin(AIAnswer, AIAnswer.ai_run_id == AIRun.id)
                .outerjoin(EvidenceAssessment, EvidenceAssessment.ai_run_id == AIRun.id)
                .where(AIRun.organization_id == member.organization_id, AIRun.id.in_(run_ids))
            )
            details = {
                row.id: AssistantRunInfo(
                    ai_run_id=row.id, answer_status=row.status, reason=row.reason
                )
                for row in rows
            }
        return StaffAssistantConversationRead(
            **AssistantConversationSummary.build(conversation).model_dump(),
            customer_name=conversation.customer.name if conversation.customer else None,
            messages=[
                StaffAssistantMessageRead.build_with(
                    m, details.get(m.ai_run_id) if m.ai_run_id else None
                )
                for m in conversation.messages
            ],
        )

    # --- Feedback and handoff ------------------------------------------------------------------

    async def rate(
        self,
        member: OrganizationMember,
        user: User,
        conversation_id: uuid.UUID,
        body: FeedbackRequest,
    ) -> AssistantConversation:
        """ "Did this answer your question?" Yes resolves the conversation (the
        self-service resolution metric counts only this); No leaves it open, and the
        customer is offered a person. Rating again replaces the rating."""
        require_role(member, OrgRole.CUSTOMER)
        conversation = await self._own(member, user, conversation_id, for_update=True)
        message = await self.session.scalar(
            select(AssistantMessage).where(
                AssistantMessage.id == body.message_id,
                AssistantMessage.conversation_id == conversation.id,
            )
        )
        if message is None:
            raise NotFoundError("Reply not found")
        if message.outcome != AssistantOutcome.ANSWERED:
            raise AppError("Only answers can be rated", code="not_an_answer")
        message.rating = AssistantRating.HELPFUL if body.helpful else AssistantRating.NOT_HELPFUL
        message.rated_at = utcnow()
        await self.session.flush()
        if conversation.status != ConversationStatus.HANDED_OFF:
            helpful = await self.session.scalar(
                select(func.count())
                .select_from(AssistantMessage)
                .where(
                    AssistantMessage.conversation_id == conversation.id,
                    AssistantMessage.rating == AssistantRating.HELPFUL,
                )
            )
            if helpful:
                if conversation.status != ConversationStatus.RESOLVED:
                    conversation.status = ConversationStatus.RESOLVED
                    conversation.resolved_at = utcnow()
            elif conversation.status == ConversationStatus.RESOLVED:
                conversation.status = ConversationStatus.OPEN
                conversation.resolved_at = None
        conversation.updated_at = utcnow()
        await self.session.commit()
        return await self._load(member.organization_id, conversation.id)

    async def hand_off(
        self,
        member: OrganizationMember,
        user: User,
        conversation_id: uuid.UUID,
        note: str | None,
        tickets: TicketService,
    ) -> tuple[AssistantConversation, Ticket]:
        """Open a request from the conversation: the customer's questions (and note)
        are its opening message; staff see the whole conversation on the ticket."""
        require_role(member, OrgRole.CUSTOMER)
        conversation = await self._own(member, user, conversation_id, for_update=True)
        if conversation.status == ConversationStatus.HANDED_OFF:
            raise ConflictError(
                "This conversation was already sent to the support team.",
                code="already_handed_off",
                details={"ticket_id": str(conversation.ticket_id)},
            )
        questions = (
            await self.session.scalars(
                select(AssistantMessage.body)
                .where(
                    AssistantMessage.conversation_id == conversation.id,
                    AssistantMessage.role == AssistantRole.CUSTOMER,
                )
                .order_by(AssistantMessage.seq)
            )
        ).all()
        message = "\n\n".join([*questions, *([note] if note else [])])[:MAX_MESSAGE]
        # Marked before the ticket's commit, which persists both: a second click waits
        # for the lock, then finds the conversation handed off.
        conversation.status = ConversationStatus.HANDED_OFF
        conversation.handed_off_at = utcnow()
        ticket = await tickets.create(
            member, user, TicketCreate(subject=conversation.title, message=message)
        )
        conversation.ticket_id = ticket.id
        await self.session.flush()
        # A staff-only event, which RLS rightly won't let a customer write: recorded
        # as a system operation, scoped to this one row of this ticket.
        async with rls_bypass(self.session):
            self.session.add(
                TicketEvent(
                    organization_id=member.organization_id,
                    ticket_id=ticket.id,
                    actor_id=user.id,
                    type=TicketEventType.SELF_SERVICE_HANDOFF,
                    data={"conversation_id": str(conversation.id), "questions": len(questions)},
                )
            )
            await self.session.flush()
        await self.session.commit()
        return await self._load(member.organization_id, conversation.id), ticket

    # --- Helpers -----------------------------------------------------------------------------

    async def _own(
        self,
        member: OrganizationMember,
        user: User,
        conversation_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> AssistantConversation:
        stmt = select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.organization_id == member.organization_id,
            AssistantConversation.user_id == user.id,
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        conversation = await self.session.scalar(stmt)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation

    async def _load(
        self, organization_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> AssistantConversation:
        conversation = await self.session.scalar(
            select(AssistantConversation)
            .options(
                selectinload(AssistantConversation.messages),
                joinedload(AssistantConversation.ticket),
            )
            .where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.organization_id == organization_id,
            )
            .execution_options(populate_existing=True)
        )
        assert conversation is not None
        return conversation

    async def _throttle(self, user: User) -> None:
        if self.rate_limiter is None:
            return
        for key, limit, window, message in (
            (
                f"assistant:customer:{user.id}:minute",
                settings.assistant_customer_limit,
                60,
                "Too many questions. Wait a moment.",
            ),
            (
                f"assistant:customer:{user.id}:day",
                settings.assistant_customer_daily_limit,
                24 * 60 * 60,
                "You've asked the assistant a lot of questions today. Use Contact support "
                "and our team will help.",
            ),
        ):
            result = await self.rate_limiter.hit(key, limit=limit, window_seconds=window)
            if not result.allowed:
                raise TooManyRequestsError(message, retry_after=result.retry_after)
