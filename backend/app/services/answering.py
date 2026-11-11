"""Answer questions from the knowledge base, with citations (the RAG baseline).

    question -> retrieval (recorded as an ``answer`` run, committed)
             -> prompt with the selected passages, numbered
             -> the language model (no database connection held while it works)
             -> citation check: numbers resolved to passages, invented ones removed
             -> answer and citations recorded with the exact prompt and settings

Module 5 adds evidence assessment around this: deciding whether to answer at all,
and checking that the answer is supported, not only that its citations are real.
"""

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.generation.citations import CheckedAnswer, check_answer
from ai_engine.generation.llm import Completion, LLMClient, LLMError, ModelStatus
from ai_engine.generation.prompt import (
    NOT_FOUND_ANSWER,
    PROMPT_VERSION,
    BuiltPrompt,
    PromptPassage,
    build_prompt,
)
from app.core.config import settings
from app.core.exceptions import TooManyRequestsError
from app.core.rate_limit import RateLimiter
from app.models import (
    AIAnswer,
    AIRun,
    AIRunKind,
    AnswerStatus,
    Citation,
    OrganizationMember,
    RetrievalResult,
    User,
)
from app.repositories.retrieval import RunRepository
from app.schemas.answers import AskRequest
from app.services.authorization import STAFF_ROLES, require_role
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _status(checked: CheckedAnswer) -> AnswerStatus:
    if checked.citations:
        return AnswerStatus.ANSWERED
    return AnswerStatus.DECLINED if checked.declined else AnswerStatus.UNCITED


class AnswerService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        retrieval: RetrievalService,
        llm: LLMClient,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.retrieval = retrieval
        self.llm = llm
        self.rate_limiter = rate_limiter
        self.runs = RunRepository(session)

    async def model_status(self, member: OrganizationMember) -> ModelStatus:
        require_role(member, *STAFF_ROLES)
        return await self.llm.status()

    async def answer(
        self, member: OrganizationMember, user: User, request: AskRequest
    ) -> tuple[AIRun, list[RetrievalResult]]:
        require_role(member, *STAFF_ROLES)
        await self._throttle(user)

        outcome = await self.retrieval.search(
            member.organization_id,
            request.query,
            strategy=request.strategy,
            rerank=request.rerank,
            top_k=request.top_k,
        )
        run = await self.retrieval.record(member, user, outcome, kind=AIRunKind.ANSWER)
        results = await self.runs.results(run.id)
        selected = [r for r in results if r.final_rank is not None and r.chunk is not None]
        built = build_prompt(
            request.query,
            [
                PromptPassage(
                    document_title=r.document.title if r.document else "Untitled",
                    page_number=r.chunk.page_number,
                    page_end=r.chunk.page_end,
                    section=r.chunk.section_heading,
                    text=r.chunk.content,
                    token_count=r.chunk.token_count,
                )
                for r in selected
                if r.chunk is not None
            ],
            token_budget=self.llm.prompt_token_budget,
        )
        # Release the connection: a local model can take a minute or more.
        await self.session.commit()

        answer, citations = await self._generate(run, built, selected)

        run.timings = {
            **run.timings,
            "generation": answer.latency_ms,
            "total": round(run.timings.get("total", 0.0) + answer.latency_ms, 2),
        }
        run.latency_ms = run.timings["total"]
        await self.runs.add_answer(answer, citations)
        await self.session.commit()

        loaded = await self.runs.get(member.organization_id, run.id)
        assert loaded is not None
        return loaded, results

    async def _generate(
        self, run: AIRun, built: BuiltPrompt, selected: list[RetrievalResult]
    ) -> tuple[AIAnswer, list[Citation]]:
        answer = AIAnswer(
            ai_run_id=run.id,
            organization_id=run.organization_id,
            provider=self.llm.provider,
            model=self.llm.model,
            config=self.llm.config,
            prompt_version=PROMPT_VERSION,
            system_prompt=built.prompt.system,
            user_prompt=built.prompt.user,
            passage_count=len(built.passages),
            omitted_passages=built.omitted,
            invalid_citations=[],
            timings={},
        )
        started = time.perf_counter()
        if not built.passages:
            answer.status = AnswerStatus.NO_PASSAGES
            answer.text = NOT_FOUND_ANSWER
            answer.latency_ms = _elapsed_ms(started)
            return answer, []

        try:
            completion = await self.llm.complete(built.prompt)
        except LLMError as exc:
            logger.warning("Answer generation failed for run %s: %s", run.id, exc)
            answer.status = AnswerStatus.FAILED
            answer.error = str(exc)
            answer.latency_ms = _elapsed_ms(started)
            answer.timings = {"total": answer.latency_ms}
            return answer, []

        self._record_completion(answer, completion)
        answer.latency_ms = _elapsed_ms(started)
        if completion.stop_reason == "refusal":
            answer.status = AnswerStatus.FAILED
            answer.error = "The model declined to answer this request."
            return answer, []
        checked = check_answer(completion.text, len(built.passages))
        if not checked.text:
            answer.status = AnswerStatus.FAILED
            answer.error = "The model returned an empty answer."
            return answer, []

        answer.status = _status(checked)
        answer.text = checked.text
        answer.invalid_citations = checked.invalid
        return answer, [
            self._citation(answer, selected[c.passage - 1], c.passage, c.start, c.end)
            for c in checked.citations
        ]

    @staticmethod
    def _record_completion(answer: AIAnswer, completion: Completion) -> None:
        answer.raw_output = completion.text
        answer.model = completion.model or answer.model
        answer.model_version = completion.model_version
        answer.stop_reason = completion.stop_reason
        answer.input_tokens = completion.input_tokens
        answer.output_tokens = completion.output_tokens
        answer.timings = completion.timings_ms

    @staticmethod
    def _citation(
        answer: AIAnswer, result: RetrievalResult, number: int, start: int, end: int
    ) -> Citation:
        chunk = result.chunk
        assert chunk is not None
        return Citation(
            id=uuid.uuid4(),
            organization_id=answer.organization_id,
            ai_run_id=answer.ai_run_id,
            retrieval_result_id=result.id,
            passage_number=number,
            answer_start=start,
            answer_end=end,
            document_id=result.document_id,
            document_version=result.document_version,
            chunk_id=result.chunk_id,
            chunk_index=result.chunk_index,
            page_number=chunk.page_number,
            page_end=chunk.page_end,
            section_heading=chunk.section_heading,
        )

    async def _throttle(self, user: User) -> None:
        if self.rate_limiter is None:
            return
        result = await self.rate_limiter.hit(
            f"ai:chat:user:{user.id}", limit=settings.ai_chat_limit, window_seconds=60
        )
        if not result.allowed:
            raise TooManyRequestsError(
                "Too many questions. Wait a moment.", retry_after=result.retry_after
            )
