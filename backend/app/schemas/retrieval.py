import uuid
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints

from ai_engine.reranking.cross_encoder import relevance
from ai_engine.retrieval.engine import RequestedStrategy
from app.models import RetrievalResult
from app.schemas.documents import ChunkRead, DocumentSummary

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
