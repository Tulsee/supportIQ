"""Answer questions from the knowledge base, with citations and evidence assessment.

    question -> retrieval (recorded as an ``answer`` run, committed)
             -> [assessed] evidence signals: relevance, coverage, vagueness
                -> abstain or ask to clarify without calling the model, when clear
             -> prompt with the selected passages, numbered
             -> the language model (no database connection held while it works);
                [assessed] replies with JSON: status, answer, conflicts, clarification
             -> citation check: numbers resolved to passages, invented ones removed
             -> [assessed] claim checks (NLI) and the decision policy:
                answer / clarify / escalate / abstain
             -> answer, citations and assessment recorded with prompt and settings

Policies: ``baseline`` (always answer; the Module 4 RAG baseline), ``assessed``
(the default), and ``observe`` (assessed, but the model is always called, so every
signal is recorded; used to tune thresholds).
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.evidence.grounding import Grounding, check_claims
from ai_engine.evidence.nli import NliModel
from ai_engine.evidence.policy import (
    POLICY_VERSION,
    Decision,
    Signals,
    Thresholds,
    Verdict,
    decide_after,
    decide_before,
    evidence_score,
)
from ai_engine.evidence.signals import coverage, top_relevance, vagueness
from ai_engine.generation.citations import CheckedAnswer, check_answer
from ai_engine.generation.conversation import Turn, contextual_query, history_block
from ai_engine.generation.llm import Completion, LLMClient, LLMError, ModelStatus
from ai_engine.generation.prompt import (
    NOT_FOUND_ANSWER,
    PROMPT_VERSION,
    REPLY_PROMPT_VERSION,
    STRUCTURED_PROMPT_VERSION,
    BuiltPrompt,
    PromptPassage,
    build_prompt,
)
from ai_engine.generation.structured import StructuredAnswer, parse_structured
from app.core.config import settings
from app.core.exceptions import TooManyRequestsError
from app.core.knowledge import policy_thresholds
from app.core.rate_limit import RateLimiter
from app.models import (
    AIAnswer,
    AIDecision,
    AIRun,
    AIRunKind,
    AnswerStatus,
    Citation,
    EvidenceAssessment,
    OrganizationMember,
    RetrievalResult,
    User,
)
from app.repositories.retrieval import RunRepository
from app.schemas.answers import AskRequest
from app.services.authorization import STAFF_ROLES, require_role
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)
run_log = logging.getLogger("supportiq.ai_runs")

Policy = Literal["baseline", "assessed", "observe"]

ABSTAIN_ANSWER = (
    "I couldn't find a reliable answer to this in the knowledge base. "
    "A team member can help with it."
)

_STATUS_OF_DECISION = {
    Decision.CLARIFY: AnswerStatus.CLARIFY,
    Decision.ESCALATE: AnswerStatus.ESCALATED,
    Decision.ABSTAIN: AnswerStatus.ABSTAINED,
}


def log_run(run: AIRun) -> None:
    """One structured line per AI run, for log-based monitoring (the run itself, with
    its prompt and every signal, is in the database and the AI inspector)."""
    answer, assessment = run.answer, run.assessment
    run_log.info(
        "ai_run id=%s kind=%s org=%s status=%s decision=%s reason=%s latency_ms=%.0f "
        "model=%s tokens_in=%s tokens_out=%s passages=%s",
        run.id,
        run.kind.value,
        run.organization_id,
        answer.status.value if answer else None,
        assessment.decision.value if assessment else None,
        assessment.reason if assessment else None,
        run.latency_ms,
        answer.model if answer else None,
        answer.input_tokens if answer else None,
        answer.output_tokens if answer else None,
        answer.passage_count if answer else None,
    )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _status(checked: CheckedAnswer) -> AnswerStatus:
    if checked.citations:
        return AnswerStatus.ANSWERED
    return AnswerStatus.DECLINED if checked.declined else AnswerStatus.UNCITED


def clarifying_question(selected: list[RetrievalResult]) -> str:
    """Ask which topic is meant, naming the documents and sections retrieved."""
    options: list[str] = []
    for result in selected[:4]:
        chunk, document = result.chunk, result.document
        if document is None:
            continue
        section = chunk.section_heading if chunk is not None else None
        label = (
            f"{section} ({document.title})"
            if section and section != document.title
            else document.title
        )
        if label not in options:
            options.append(label)
    if len(options) < 2:
        return (
            "Could you tell me a bit more about what you need, for example which product or policy?"
        )
    listed = ", ".join(options[:-1]) + f" or {options[-1]}"
    return f"Could you tell me a bit more about what you mean? For example: {listed}?"


@dataclass
class _Outcome:
    answer: AIAnswer
    citations: list[Citation]
    assessment: EvidenceAssessment | None = None


class AnswerService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        retrieval: RetrievalService,
        llm: LLMClient,
        nli: NliModel | None = None,
        rate_limiter: RateLimiter | None = None,
        policy: Policy | None = None,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.session = session
        self.retrieval = retrieval
        self.llm = llm
        self.nli = nli
        self.rate_limiter = rate_limiter
        self.policy: Policy = policy or settings.ai_decision_policy
        self.thresholds = thresholds or policy_thresholds()
        self.runs = RunRepository(session)
        self._prompt_version = STRUCTURED_PROMPT_VERSION

    async def model_status(self, member: OrganizationMember) -> ModelStatus:
        require_role(member, *STAFF_ROLES)
        return await self.llm.status()

    async def answer(
        self, member: OrganizationMember, user: User, request: AskRequest
    ) -> tuple[AIRun, list[RetrievalResult]]:
        require_role(member, *STAFF_ROLES)
        await self._throttle(user)
        return await self.answer_question(member, user, request)

    async def answer_question(
        self,
        member: OrganizationMember,
        user: User,
        request: AskRequest,
        *,
        kind: AIRunKind = AIRunKind.ANSWER,
        ticket_id: uuid.UUID | None = None,
        audience: Literal["staff", "customer"] = "staff",
        search_query: str | None = None,
    ) -> tuple[AIRun, list[RetrievalResult]]:
        """The pipeline without access checks or rate limits (callers check).

        ``search_query`` is what retrieval searches for (default: the question, joined
        with the previous one when it is a follow-up); the model answers the question
        itself, with the earlier turns of ``request.history`` for context."""
        history = [Turn(t.question, t.answer) for t in request.history]
        query = search_query or contextual_query(request.query, history)
        outcome = await self.retrieval.search(
            member.organization_id,
            query,
            strategy=request.strategy,
            rerank=request.rerank,
            top_k=request.top_k,
        )
        run = await self.retrieval.record(member, user, outcome, kind=kind, ticket_id=ticket_id)
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
            structured=self.policy != "baseline",
            audience=audience,
            context=history_block(history),
        )
        self._prompt_version = (
            PROMPT_VERSION
            if self.policy == "baseline"
            else REPLY_PROMPT_VERSION
            if audience == "customer"
            else STRUCTURED_PROMPT_VERSION
        )
        # Release the connection: a local model can take a minute or more.
        await self.session.commit()

        if self.policy == "baseline":
            result = await self._baseline(run, built, selected)
        else:
            result = await self._assessed(run, built, selected, query)

        answer = result.answer
        run.timings = {
            **run.timings,
            "generation": answer.latency_ms,
            "total": round(run.timings.get("total", 0.0) + answer.latency_ms, 2),
        }
        run.latency_ms = run.timings["total"]
        await self.runs.add_answer(answer, result.citations)
        if result.assessment is not None:
            self.session.add(result.assessment)
        await self.session.commit()

        loaded = await self.runs.get(member.organization_id, run.id)
        assert loaded is not None
        log_run(loaded)
        return loaded, results

    # --- Baseline: always answer -------------------------------------------------------------

    def _new_answer(self, run: AIRun, built: BuiltPrompt, prompt_version: str) -> AIAnswer:
        return AIAnswer(
            ai_run_id=run.id,
            organization_id=run.organization_id,
            provider=self.llm.provider,
            model=self.llm.model,
            config=self.llm.config,
            prompt_version=prompt_version,
            system_prompt=built.prompt.system,
            user_prompt=built.prompt.user,
            passage_count=len(built.passages),
            omitted_passages=built.omitted,
            invalid_citations=[],
            timings={},
        )

    async def _complete(self, answer: AIAnswer, built: BuiltPrompt) -> Completion | None:
        try:
            completion = await self.llm.complete(built.prompt)
        except LLMError as exc:
            logger.warning("Answer generation failed for run %s: %s", answer.ai_run_id, exc)
            answer.status = AnswerStatus.FAILED
            answer.error = str(exc)
            return None
        answer.raw_output = completion.text
        answer.model = completion.model or answer.model
        answer.model_version = completion.model_version
        answer.stop_reason = completion.stop_reason
        answer.input_tokens = completion.input_tokens
        answer.output_tokens = completion.output_tokens
        answer.timings = completion.timings_ms
        if completion.stop_reason == "refusal":
            answer.status = AnswerStatus.FAILED
            answer.error = "The model declined to answer this request."
            return None
        return completion

    async def _baseline(
        self, run: AIRun, built: BuiltPrompt, selected: list[RetrievalResult]
    ) -> _Outcome:
        answer = self._new_answer(run, built, PROMPT_VERSION)
        started = time.perf_counter()
        if not built.passages:
            answer.status = AnswerStatus.NO_PASSAGES
            answer.text = NOT_FOUND_ANSWER
            answer.latency_ms = _elapsed_ms(started)
            return _Outcome(answer, [])
        completion = await self._complete(answer, built)
        answer.latency_ms = _elapsed_ms(started)
        if completion is None:
            answer.timings = answer.timings or {"total": answer.latency_ms}
            return _Outcome(answer, [])
        checked = check_answer(completion.text, len(built.passages))
        if not checked.text:
            answer.status = AnswerStatus.FAILED
            answer.error = "The model returned an empty answer."
            return _Outcome(answer, [])
        answer.status = _status(checked)
        answer.text = checked.text
        answer.invalid_citations = checked.invalid
        return _Outcome(answer, self._citations(answer, checked, selected))

    # --- Assessed: decide from the evidence ------------------------------------------------------

    def _signals_before(
        self, question: str, built: BuiltPrompt, selected: list[RetrievalResult]
    ) -> Signals:
        used = selected[: len(built.passages)]
        best, mean = top_relevance([r.rerank_score for r in used])
        covered, missing = coverage(question, [p.text for p in built.passages])
        vague, _ = vagueness(question)
        return Signals(
            passages=len(built.passages),
            relevance=round(best, 4) if best is not None else None,
            relevance_mean=round(mean, 4) if mean is not None else None,
            coverage=round(covered, 4),
            missing_terms=missing,
            vague=vague,
            documents=len({r.document_id for r in used}),
        )

    async def _assessed(
        self,
        run: AIRun,
        built: BuiltPrompt,
        selected: list[RetrievalResult],
        question: str,
    ) -> _Outcome:
        started = time.perf_counter()
        answer = self._new_answer(run, built, self._prompt_version)
        signals = self._signals_before(question, built, selected)
        verdict = decide_before(signals, self.thresholds)
        if verdict is not None and (self.policy == "assessed" or signals.passages == 0):
            answer.latency_ms = _elapsed_ms(started)
            answer.timings = {"total": answer.latency_ms}
            return self._finish(answer, verdict, signals, None, None, selected, started)

        model_started = time.perf_counter()
        completion = await self._complete(answer, built)
        model_ms = _elapsed_ms(model_started)
        if completion is None:
            answer.latency_ms = _elapsed_ms(started)
            failed = Verdict(Decision.ABSTAIN, "model_error", "after")
            return self._finish(
                answer, failed, signals, None, None, selected, started, model_ms=model_ms
            )

        parsed = parse_structured(completion.text)
        checked = check_answer(parsed.answer, len(built.passages)) if parsed.answer else None
        signals.generated = True
        signals.model_status = self._model_status(parsed, checked)
        if checked is not None:
            signals.citations = len(checked.citations)
            signals.invalid_citations = len(checked.invalid)
            answer.invalid_citations = checked.invalid
        conflicting = {
            selected[n - 1].document_id
            for n in parsed.conflicting_passages
            if 1 <= n <= len(built.passages)
        }
        signals.conflict_documents = len(conflicting)

        grounding = None
        if checked is not None and checked.citations and self.nli is not None:
            texts = [p.text for p in built.passages]
            try:
                grounding = await asyncio.to_thread(check_claims, checked, texts, self.nli)
            except Exception:  # the claim checker is optional; never fail the answer
                logger.exception("Claim checks failed for run %s", run.id)
            if grounding is not None:
                signals.grounding = grounding.entailment
                signals.contradiction = grounding.contradiction

        if verdict is None:  # observe mode keeps a decision made before generation
            verdict = decide_after(signals, self.thresholds)
        answer.latency_ms = _elapsed_ms(started)
        return self._finish(
            answer, verdict, signals, parsed, checked, selected, started, grounding, model_ms
        )

    @staticmethod
    def _model_status(parsed: StructuredAnswer, checked: CheckedAnswer | None) -> str:
        if parsed.status != "unstructured":
            return parsed.status
        # A model without structured output: infer what it did from the text.
        if checked is None or checked.declined:
            return "not_found"
        return "answered"

    def _finish(
        self,
        answer: AIAnswer,
        verdict: Verdict,
        signals: Signals,
        parsed: StructuredAnswer | None,
        checked: CheckedAnswer | None,
        selected: list[RetrievalResult],
        started: float,
        grounding: Grounding | None = None,
        model_ms: float = 0.0,
    ) -> _Outcome:
        """`model_ms` (the model call) is left out of the assessment's own time."""
        citations: list[Citation] = []
        if answer.status != AnswerStatus.FAILED:
            if verdict.decision is Decision.ANSWER and checked is not None:
                answer.status = AnswerStatus.ANSWERED
                answer.text = checked.text
                citations = self._citations(answer, checked, selected)
            elif verdict.decision is Decision.ESCALATE and checked is not None:
                # What each source says, with citations, for the person who decides.
                answer.status = AnswerStatus.ESCALATED
                answer.text = checked.text
                citations = self._citations(answer, checked, selected)
            elif verdict.decision is Decision.CLARIFY:
                answer.status = AnswerStatus.CLARIFY
                answer.text = (
                    parsed.clarifying_question
                    if parsed is not None and parsed.clarifying_question
                    else clarifying_question(selected)
                )
            elif verdict.reason == "no_passages":
                answer.status = AnswerStatus.NO_PASSAGES
                answer.text = NOT_FOUND_ANSWER
            else:
                answer.status = _STATUS_OF_DECISION.get(verdict.decision, AnswerStatus.ABSTAINED)
                answer.text = ABSTAIN_ANSWER
        assessment = EvidenceAssessment(
            ai_run_id=answer.ai_run_id,
            organization_id=answer.organization_id,
            policy=self.policy,
            policy_version=POLICY_VERSION,
            decision=AIDecision(verdict.decision.value),
            reason=verdict.reason,
            stage=verdict.stage,
            relevance=signals.relevance,
            coverage=signals.coverage,
            consistency=signals.consistency if signals.generated else None,
            grounding=signals.grounding,
            contradiction=signals.contradiction,
            ambiguity=1.0 if signals.vague else 0.0,
            score=evidence_score(signals),
            signals=signals.as_dict(),
            thresholds=self.thresholds.as_dict(),
            claims=[
                {
                    "claim": c.claim,
                    "passages": c.passages,
                    "entailment": c.entailment,
                    "contradiction": c.contradiction,
                }
                for c in (grounding.claims if grounding else [])
            ],
            nli_model=self.nli.model_name if self.nli is not None and grounding else None,
            latency_ms=round(max(0.0, _elapsed_ms(started) - model_ms), 2),
        )
        return _Outcome(answer, citations, assessment)

    # --- Shared ----------------------------------------------------------------------------------

    def _citations(
        self, answer: AIAnswer, checked: CheckedAnswer, selected: list[RetrievalResult]
    ) -> list[Citation]:
        return [
            self._citation(answer, selected[c.passage - 1], c.passage, c.start, c.end)
            for c in checked.citations
        ]

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
