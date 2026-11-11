import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, Field, StringConstraints

from ai_engine.reranking.cross_encoder import relevance
from ai_engine.retrieval.engine import RequestedStrategy
from app.models import AIRun, RetrievalResult
from app.schemas.documents import ChunkRead, DocumentSummary
from app.schemas.tickets import PersonSummary

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)]


class RetrieveRequest(BaseModel):
    query: Query
    strategy: RequestedStrategy = Field(
        default=RequestedStrategy.AUTO,
        description="`auto` picks a strategy per query (adaptive retrieval); the others "
        "are fixed, for comparison.",
    )
    rerank: bool = Field(default=True, description="Reorder candidates with the cross-encoder.")
    top_k: int = Field(default=5, ge=1, le=20, description="Passages to select.")


class StageScore(BaseModel):
    rank: int
    score: float


class RetrievedPassage(BaseModel):
    final_rank: int | None = Field(description="Set for the selected passages, 1 = best.")
    fused_rank: int
    fused_score: float = Field(description="Weighted Reciprocal Rank Fusion score.")
    sparse: StageScore | None = Field(description="BM25 rank and score, if it found this.")
    dense: StageScore | None = Field(description="Vector-search rank and cosine similarity.")
    rerank_score: float | None = Field(description="Cross-encoder score (a logit).")
    relevance: float | None = Field(description="The cross-encoder score mapped to 0-1.")
    document_id: uuid.UUID | None
    document_version: int
    chunk_index: int
    document: DocumentSummary | None
    chunk: ChunkRead | None = Field(
        description="The passage; null if it has been replaced by a re-index since the run."
    )

    @classmethod
    def build(cls, result: RetrievalResult) -> Self:
        document = result.document
        return cls(
            final_rank=result.final_rank,
            fused_rank=result.fused_rank,
            fused_score=result.fused_score,
            sparse=(
                StageScore(rank=result.sparse_rank, score=result.sparse_score or 0.0)
                if result.sparse_rank is not None
                else None
            ),
            dense=(
                StageScore(rank=result.dense_rank, score=result.dense_score or 0.0)
                if result.dense_rank is not None
                else None
            ),
            rerank_score=result.rerank_score,
            relevance=relevance(result.rerank_score) if result.rerank_score is not None else None,
            document_id=result.document_id,
            document_version=result.document_version,
            chunk_index=result.chunk_index,
            document=(
                DocumentSummary(id=document.id, title=document.title, file_type=document.file_type)
                if document is not None
                else None
            ),
            chunk=ChunkRead.build(result.chunk) if result.chunk is not None else None,
        )


class RetrievalRunSummary(BaseModel):
    id: uuid.UUID
    query: str
    query_type: str
    requested_strategy: str
    strategy: str
    latency_ms: float
    result_count: int
    user: PersonSummary | None
    created_at: datetime

    @classmethod
    def build(cls, run: AIRun) -> Self:
        return cls(
            id=run.id,
            query=run.query,
            query_type=run.query_type,
            requested_strategy=run.requested_strategy,
            strategy=run.strategy,
            latency_ms=run.latency_ms,
            result_count=run.result_count,
            user=PersonSummary.build(run.user),
            created_at=run.created_at,
        )


class RetrievalRunRead(RetrievalRunSummary):
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

    @classmethod
    def build_with(cls, run: AIRun, results: list[RetrievalResult]) -> Self:
        summary = RetrievalRunSummary.build(run).model_dump()
        passages = [RetrievedPassage.build(result) for result in results]
        return cls(
            **summary,
            reasons=run.config.get("reasons", []),
            config=run.config,
            timings_ms=run.timings,
            degraded=run.config.get("degraded", {}),
            results=[p for p in passages if p.final_rank is not None],
            other_candidates=[p for p in passages if p.final_rank is None],
        )
