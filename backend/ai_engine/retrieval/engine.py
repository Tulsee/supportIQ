"""The retrieval pipeline.

    query -> analysis -> BM25 and/or dense search (concurrently; if one fails,
             the run continues with the other and records the failure)
          -> weighted Reciprocal Rank Fusion
          -> resolve against current chunks (drops stale vector hits)
          -> cross-encoder reranking of the head of the list -> top K

Every candidate keeps its scores from every stage, and each stage is timed, so a
run can be recorded and inspected (and strategies compared in experiments).
"""

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ai_engine.embeddings.embedder import Embedder
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.fusion import RankedChunk, reciprocal_rank_fusion
from ai_engine.retrieval.query_analysis import WEIGHTS, QueryAnalysis, Strategy, analyze
from ai_engine.retrieval.vector_store import QdrantVectorStore

SPARSE, DENSE = "sparse", "dense"


class RequestedStrategy(StrEnum):
    AUTO = "auto"  # adaptive: chosen per query by the analysis
    SPARSE = "sparse"
    DENSE = "dense"
    HYBRID = "hybrid"


class RetrievalUnavailableError(Exception):
    """Every retriever the strategy needed failed."""

    def __init__(self, failures: dict[str, str]) -> None:
        super().__init__("; ".join(f"{name}: {error}" for name, error in failures.items()))
        self.failures = failures


class Retriever(Protocol):
    async def search(self, query: str, limit: int) -> list[RankedChunk]: ...


class PassageLoader(Protocol):
    async def load(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Text of the chunks that still exist; replaced or deleted ids are absent."""
        ...


@dataclass(frozen=True)
class RetrievalConfig:
    sparse_top_k: int = 20
    dense_top_k: int = 20
    rrf_k: int = 60
    rerank: bool = True
    rerank_candidates: int = 20  # how much of the fused list the cross-encoder reorders
    top_k: int = 5


@dataclass
class Candidate:
    chunk_id: uuid.UUID
    fused_rank: int
    fused_score: float
    sparse: tuple[int, float] | None = None  # (rank, BM25 score)
    dense: tuple[int, float] | None = None  # (rank, cosine similarity)
    rerank_score: float | None = None
    final_rank: int | None = None  # set for the selected top K


@dataclass
class RetrievalOutcome:
    query: str
    requested: RequestedStrategy
    analysis: QueryAnalysis
    strategy: Strategy
    weights: dict[str, float]
    config: RetrievalConfig
    reranker: str | None
    candidates: list[Candidate]  # final order: selected first, then the rest
    timings_ms: dict[str, float] = field(default_factory=dict)
    # Retrievers that failed; the run continued with the others (degraded).
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def selected(self) -> list[Candidate]:
        return [c for c in self.candidates if c.final_rank is not None]


class DenseRetriever:
    """Vector search within one organization; with ``document_ids``, only within those
    documents (the customer assistant passes the public ones)."""

    def __init__(
        self,
        embedder: Embedder,
        store: QdrantVectorStore,
        organization_id: uuid.UUID,
        *,
        document_ids: Sequence[uuid.UUID] | None = None,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.organization_id = organization_id
        self.document_ids = document_ids

    async def search(self, query: str, limit: int) -> list[RankedChunk]:
        if self.document_ids is not None and not self.document_ids:
            return []
        vector = await asyncio.to_thread(self.embedder.embed_query, query)
        hits = await self.store.search(
            self.organization_id, vector, limit=limit, document_ids=self.document_ids
        )
        return [RankedChunk(hit.chunk_id, hit.score) for hit in hits]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


class RetrievalEngine:
    def __init__(
        self,
        *,
        sparse: Retriever,
        dense: Retriever,
        passages: PassageLoader,
        reranker: Reranker | None,
        config: RetrievalConfig,
    ) -> None:
        self.sparse = sparse
        self.dense = dense
        self.passages = passages
        self.reranker = reranker
        self.config = config

    async def retrieve(
        self, query: str, requested: RequestedStrategy = RequestedStrategy.AUTO
    ) -> RetrievalOutcome:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        config = self.config

        analysis = analyze(query)
        if requested is RequestedStrategy.AUTO:
            strategy, multiplier = analysis.strategy, analysis.candidate_multiplier
        else:
            strategy, multiplier = Strategy(requested.value), 1
        sparse_weight, dense_weight = WEIGHTS[strategy]
        timings["analysis"] = _elapsed_ms(started)

        async def timed(name: str, retriever: Retriever, limit: int) -> list[RankedChunk]:
            stage = time.perf_counter()
            try:
                return await retriever.search(query, limit * multiplier)
            finally:
                timings[name] = _elapsed_ms(stage)

        searches = {}
        if sparse_weight > 0:
            searches[SPARSE] = timed(SPARSE, self.sparse, config.sparse_top_k)
        if dense_weight > 0:
            searches[DENSE] = timed(DENSE, self.dense, config.dense_top_k)
        results = await asyncio.gather(*searches.values(), return_exceptions=True)
        rankings: dict[str, list[RankedChunk]] = {}
        failures: dict[str, str] = {}
        for name, result in zip(searches, results, strict=True):
            if isinstance(result, BaseException):
                failures[name] = f"{type(result).__name__}: {result}"
            else:
                rankings[name] = result
        if not rankings:  # nothing to fall back on
            raise RetrievalUnavailableError(failures)

        stage = time.perf_counter()
        fused = reciprocal_rank_fusion(
            rankings, {SPARSE: sparse_weight, DENSE: dense_weight}, k=config.rrf_k
        )
        rerank = config.rerank and self.reranker is not None
        head = fused[: config.rerank_candidates if rerank else config.top_k * 3]
        texts = await self.passages.load([entry.chunk_id for entry in head])
        live = [entry for entry in head if entry.chunk_id in texts]
        candidates = [
            Candidate(
                chunk_id=entry.chunk_id,
                fused_rank=rank,
                fused_score=entry.score,
                sparse=entry.sources.get(SPARSE),
                dense=entry.sources.get(DENSE),
            )
            for rank, entry in enumerate(live, start=1)
        ]
        timings["fusion"] = _elapsed_ms(stage)

        if rerank and candidates:
            assert self.reranker is not None
            stage = time.perf_counter()
            scores = await asyncio.to_thread(
                self.reranker.score, query, [texts[c.chunk_id] for c in candidates]
            )
            for candidate, score in zip(candidates, scores, strict=True):
                candidate.rerank_score = score
            candidates.sort(key=lambda c: c.rerank_score or 0.0, reverse=True)
            timings["rerank"] = _elapsed_ms(stage)
        for rank, candidate in enumerate(candidates[: config.top_k], start=1):
            candidate.final_rank = rank

        timings["total"] = _elapsed_ms(started)
        return RetrievalOutcome(
            query=query,
            requested=requested,
            analysis=analysis,
            strategy=strategy,
            weights={SPARSE: sparse_weight, DENSE: dense_weight},
            config=config,
            reranker=self.reranker.model_name if rerank and self.reranker else None,
            candidates=candidates,
            timings_ms=timings,
            failures=failures,
        )
