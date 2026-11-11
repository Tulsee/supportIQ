"""Recorded AI runs: searches and answers, for the AI inspector."""

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, Field

from app.models import AIRun, AIRunKind, AnswerStatus, RetrievalResult
from app.schemas.answers import AnswerRead
from app.schemas.retrieval import RetrievedPassage
from app.schemas.tickets import PersonSummary


class AIRunSummary(BaseModel):
    id: uuid.UUID
    kind: AIRunKind = Field(
        description="`retrieval`: a search; `answer`: a search, then an answer."
    )
    query: str
    query_type: str
    requested_strategy: str
    strategy: str
    latency_ms: float
    result_count: int
    answer_status: AnswerStatus | None = Field(description="For `answer` runs.")
    user: PersonSummary | None
    created_at: datetime

    @classmethod
    def build(cls, run: AIRun) -> Self:
        return cls(
            id=run.id,
            kind=run.kind,
            query=run.query,
            query_type=run.query_type,
            requested_strategy=run.requested_strategy,
            strategy=run.strategy,
            latency_ms=run.latency_ms,
            result_count=run.result_count,
            answer_status=run.answer.status if run.answer is not None else None,
            user=PersonSummary.build(run.user),
            created_at=run.created_at,
        )


class AIRunRead(AIRunSummary):
    reasons: list[str] = Field(description="Why the query analysis chose this strategy.")
    config: dict[str, Any] = Field(
        description="Models, top-k values, fusion weights, BM25 parameters: enough to "
        "reproduce the run."
    )
    timings_ms: dict[str, float]
    degraded: dict[str, str] = Field(
        description="Retrievers that failed during the run (it continued without them)."
    )
    results: list[RetrievedPassage] = Field(description="The selected passages, best first.")
    other_candidates: list[RetrievedPassage] = Field(
        description="Candidates considered but not selected, by fused rank."
    )
    answer: AnswerRead | None = Field(
        description="For `answer` runs: the answer, its citations, and the exact prompt "
        "and model settings. Null for searches, and for a run whose answer was never "
        "recorded (the server stopped while the model was working)."
    )

    @classmethod
    def build_with(cls, run: AIRun, results: list[RetrievalResult]) -> Self:
        summary = AIRunSummary.build(run).model_dump()
        passages = [RetrievedPassage.build(result) for result in results]
        return cls(
            **summary,
            reasons=run.config.get("reasons", []),
            config=run.config,
            timings_ms=run.timings,
            degraded=run.config.get("degraded", {}),
            results=[p for p in passages if p.final_rank is not None],
            other_candidates=[p for p in passages if p.final_rank is None],
            answer=AnswerRead.build(run.answer) if run.answer is not None else None,
        )
