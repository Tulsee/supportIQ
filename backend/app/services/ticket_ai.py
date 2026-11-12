"""AI in ticket conversations (Module 6): reply suggestions and summaries for agents.

- Suggest a reply: the latest customer message (with the subject, and the message
  before it for a short follow-up) goes through the evidence-assessed answer
  pipeline, written as a draft reply to the customer. Agents review and send it;
  nothing is sent automatically. When the AI can't draft a supported reply
  (escalate or abstain), the ticket records a staff-only "AI escalated" event.
- Summarize: the conversation (internal notes included) as 3-5 bullet points.

Both are recorded as AI runs linked to the ticket, so they appear in the AI
inspector with their prompts, model settings and timings.
"""

import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.evidence.nli import NliModel
from ai_engine.generation.conversation import (
    SUMMARY_PROMPT_VERSION,
    ThreadMessage,
    summary_prompt,
    ticket_query,
)
from ai_engine.generation.llm import LLMClient, LLMError
from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, TooManyRequestsError
from app.core.rate_limit import RateLimiter
from app.models import (
    AIAnswer,
    AIDecision,
    AIRun,
    AIRunKind,
    AnswerStatus,
    MessageAuthorType,
    NotificationKind,
    OrganizationMember,
    RetrievalResult,
    Ticket,
    TicketEvent,
    TicketEventType,
    User,
)
from app.repositories.retrieval import RunRepository
from app.repositories.tickets import TicketRepository
from app.schemas.answers import AskRequest
from app.services.answering import AnswerService, log_run
from app.services.authorization import STAFF_ROLES, require_role
from app.services.notifications import NewNotification, NotificationService
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

_MAX_MESSAGES = 200


class TicketAIService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        llm: LLMClient | None = None,
        retrieval: RetrievalService | None = None,
        nli: NliModel | None = None,
        rate_limiter: RateLimiter | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.notifications = notifications
        self.llm = llm
        self.retrieval = retrieval
        self.nli = nli
        self.rate_limiter = rate_limiter
        self.tickets = TicketRepository(session)
        self.runs = RunRepository(session)

    async def _ticket(self, member: OrganizationMember, ticket_id: uuid.UUID) -> Ticket:
        require_role(member, *STAFF_ROLES)
        ticket = await self.tickets.get(member.organization_id, ticket_id)
        if ticket is None:
            raise NotFoundError("Ticket not found")
        return ticket

    async def _thread(self, ticket: Ticket) -> list[ThreadMessage]:
        messages, _ = await self.tickets.list_messages(
            ticket.id, include_internal=True, limit=_MAX_MESSAGES, offset=0
        )
        return [
            ThreadMessage(
                author="customer" if m.author_type == MessageAuthorType.CUSTOMER else "agent",
                body=m.body,
                internal=m.is_internal,
            )
            for m in messages
        ]

    # --- Suggest a reply ---------------------------------------------------------------------

    async def suggest_reply(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID
    ) -> tuple[AIRun, list[RetrievalResult]]:
        ticket = await self._ticket(member, ticket_id)
        await self._throttle(user)
        thread = await self._thread(ticket)
        if not any(m.author == "customer" for m in thread):
            raise ConflictError("The customer hasn't written anything to reply to yet.")
        query = ticket_query(ticket.subject, thread)
        assert self.retrieval is not None and self.llm is not None
        service = AnswerService(
            self.session, retrieval=self.retrieval, llm=self.llm, nli=self.nli, policy="assessed"
        )
        latest = next(m.body for m in reversed(thread) if m.author == "customer")
        run, results = await service.answer_question(
            member,
            user,
            AskRequest(query=latest[:500] if len(latest) >= 2 else query),
            kind=AIRunKind.SUGGESTION,
            ticket_id=ticket.id,
            audience="customer",
            search_query=query,
        )
        assessment = run.assessment
        if assessment is not None and assessment.decision in (
            AIDecision.ESCALATE,
            AIDecision.ABSTAIN,
        ):
            await self._record_escalation(ticket, user, run)
        return run, results

    async def _record_escalation(self, ticket: Ticket, user: User, run: AIRun) -> None:
        fresh = await self.tickets.get(ticket.organization_id, ticket.id)
        if fresh is None:
            return
        event = TicketEvent(
            organization_id=fresh.organization_id,
            actor_id=user.id,
            type=TicketEventType.AI_ESCALATED,
            data={
                "run_id": str(run.id),
                "decision": run.assessment.decision.value if run.assessment else None,
                "reason": run.assessment.reason if run.assessment else None,
            },
        )
        event.ticket = fresh
        self.tickets.add(event)
        if self.notifications is not None and fresh.assignee_id is not None:
            await self.notifications.add(
                fresh.organization_id,
                [
                    NewNotification(
                        user_id=fresh.assignee_id,
                        kind=NotificationKind.AI_ESCALATED,
                        title=f"The AI couldn't draft a reply for #{fresh.number}",
                        body=fresh.subject,
                        ticket_id=fresh.id,
                        data={"run_id": str(run.id)},
                        dedupe_key=f"ai_escalated:{run.id}",
                    )
                ],
                actor_id=user.id,
            )
        await self.session.commit()
        if self.notifications is not None:
            await self.notifications.flush()

    # --- Summarize ---------------------------------------------------------------------------

    async def summarize(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID
    ) -> AIRun:
        ticket = await self._ticket(member, ticket_id)
        await self._throttle(user)
        thread = await self._thread(ticket)
        if not thread:
            raise ConflictError("There is no conversation to summarize yet.")
        assert self.llm is not None
        prompt = summary_prompt(ticket.subject, ticket.customer.name, ticket.status.value, thread)
        run = AIRun(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            user_id=user.id,
            ticket_id=ticket.id,
            kind=AIRunKind.SUMMARY,
            query=f"Summary of ticket #{ticket.number}: {ticket.subject}"[:500],
            query_type="summary",
            requested_strategy="none",
            strategy="none",
            config={"messages": len(thread)},
            timings={},
            latency_ms=0.0,
            result_count=0,
        )
        run.user = user
        run.answer = None
        run.assessment = None
        self.session.add(run)
        await self.session.commit()  # release the connection while the model works

        answer = AIAnswer(
            ai_run_id=run.id,
            organization_id=run.organization_id,
            provider=self.llm.provider,
            model=self.llm.model,
            config=self.llm.config,
            prompt_version=SUMMARY_PROMPT_VERSION,
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            passage_count=0,
            omitted_passages=0,
            invalid_citations=[],
            timings={},
        )
        started = time.perf_counter()
        try:
            completion = await self.llm.complete(prompt)
        except LLMError as exc:
            logger.warning("Ticket summary failed for %s: %s", ticket.id, exc)
            answer.status = AnswerStatus.FAILED
            answer.error = str(exc)
        else:
            answer.raw_output = completion.text
            answer.model = completion.model or answer.model
            answer.model_version = completion.model_version
            answer.stop_reason = completion.stop_reason
            answer.input_tokens = completion.input_tokens
            answer.output_tokens = completion.output_tokens
            answer.timings = completion.timings_ms
            text = completion.text.strip()
            answer.status = AnswerStatus.ANSWERED if text else AnswerStatus.FAILED
            answer.text = text or None
            answer.error = None if text else "The model returned an empty summary."
        answer.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        run.timings = {"generation": answer.latency_ms, "total": answer.latency_ms}
        run.latency_ms = answer.latency_ms
        await self.runs.add_answer(answer, [])
        await self.session.commit()
        loaded = await self.runs.get(member.organization_id, run.id)
        assert loaded is not None
        log_run(loaded)
        return loaded

    # --- Reading -----------------------------------------------------------------------------

    async def latest(
        self, member: OrganizationMember, ticket_id: uuid.UUID
    ) -> tuple[AIRun | None, AIRun | None]:
        """The newest suggestion and summary for a ticket."""
        ticket = await self._ticket(member, ticket_id)

        async def newest(kind: AIRunKind) -> AIRun | None:
            run_id = await self.session.scalar(
                select(AIRun.id)
                .where(
                    AIRun.organization_id == member.organization_id,
                    AIRun.ticket_id == ticket.id,
                    AIRun.kind == kind,
                )
                .order_by(AIRun.created_at.desc(), AIRun.id.desc())
                .limit(1)
            )
            return await self.runs.get(member.organization_id, run_id) if run_id else None

        return await newest(AIRunKind.SUGGESTION), await newest(AIRunKind.SUMMARY)

    async def _throttle(self, user: User) -> None:
        if self.rate_limiter is None:
            return
        result = await self.rate_limiter.hit(
            f"ai:chat:user:{user.id}", limit=settings.ai_chat_limit, window_seconds=60
        )
        if not result.allowed:
            raise TooManyRequestsError(
                "Too many AI requests. Wait a moment.", retry_after=result.retry_after
            )
