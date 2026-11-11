"""Evidence retrieval: run the engine for an organization and record the run."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import Embedder
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.engine import (
    DenseRetriever,
    RequestedStrategy,
    RetrievalConfig,
    RetrievalEngine,
    RetrievalOutcome,
    RetrievalUnavailableError,
)
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.exceptions import NotFoundError, ServiceUnavailableError, TooManyRequestsError
from app.core.rate_limit import RateLimiter
from app.models import AIRun, AIRunKind, OrganizationMember, RetrievalResult, User
from app.repositories.documents import DocumentRepository
from app.repositories.retrieval import BM25Retriever, ChunkPassages, RunRepository
from app.schemas.retrieval import RetrieveRequest
from app.services.authorization import STAFF_ROLES, require_role

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedder: Embedder | None = None,
        vector_store: QdrantVectorStore | None = None,
        reranker: Reranker | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.embedder = embedder
        self.vectors = vector_store
        self.reranker = reranker
        self.rate_limiter = rate_limiter
        self.runs = RunRepository(session)
        self.documents = DocumentRepository(session)

    async def retrieve(
        self, member: OrganizationMember, user: User, request: RetrieveRequest
    ) -> tuple[AIRun, list[RetrievalResult]]:
        require_role(member, *STAFF_ROLES)
        await self._throttle(user)
        outcome = await self.search(
            member.organization_id,
            request.query,
            strategy=request.strategy,
            rerank=request.rerank,
            top_k=request.top_k,
        )
        run = await self.record(member, user, outcome, kind=AIRunKind.RETRIEVAL)
        await self.session.commit()
        return run, await self.runs.results(run.id)

    async def search(
        self,
        organization_id: uuid.UUID,
        query: str,
        *,
        strategy: RequestedStrategy,
        rerank: bool,
        top_k: int,
    ) -> RetrievalOutcome:
        """Run the retrieval pipeline (no access checks, nothing recorded)."""
        assert self.embedder is not None and self.vectors is not None
        config = RetrievalConfig(
            sparse_top_k=settings.retrieval_sparse_top_k,
            dense_top_k=settings.retrieval_dense_top_k,
            rrf_k=settings.retrieval_rrf_k,
            rerank=rerank,
            rerank_candidates=max(settings.retrieval_rerank_candidates, top_k),
            top_k=top_k,
        )
        engine = RetrievalEngine(
            sparse=BM25Retriever(
                self.session, organization_id, k1=settings.bm25_k1, b=settings.bm25_b
            ),
            dense=DenseRetriever(self.embedder, self.vectors, organization_id),
            passages=ChunkPassages(self.session, organization_id),
            reranker=self.reranker if rerank else None,
            config=config,
        )
        try:
            return await engine.retrieve(query, strategy)
        except RetrievalUnavailableError as exc:
            logger.warning("Retrieval failed: %s", exc)
            raise ServiceUnavailableError("Search is temporarily unavailable.") from exc

    async def get_run(
        self, member: OrganizationMember, run_id: uuid.UUID
    ) -> tuple[AIRun, list[RetrievalResult]]:
        require_role(member, *STAFF_ROLES)
        run = await self.runs.get(member.organization_id, run_id)
        if run is None:
            raise NotFoundError("Run not found")
        return run, await self.runs.results(run.id)

    async def list_runs(
        self,
        member: OrganizationMember,
        *,
        kind: AIRunKind | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[AIRun], int]:
        require_role(member, *STAFF_ROLES)
        return await self.runs.find(member.organization_id, kind=kind, limit=limit, offset=offset)

    async def record(
        self, member: OrganizationMember, user: User, outcome: RetrievalOutcome, *, kind: AIRunKind
    ) -> AIRun:
        """Add the run and every candidate's scores to the session (not committed)."""
        assert self.embedder is not None
        chunks = await self.documents.chunks_by_id(
            member.organization_id, [c.chunk_id for c in outcome.candidates]
        )
        run = AIRun(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            user_id=user.id,
            kind=kind,
            query=outcome.query,
            query_type=outcome.analysis.query_type.value,
            requested_strategy=outcome.requested.value,
            strategy=outcome.strategy.value,
            config={
                "reasons": outcome.analysis.reasons,
                "weights": outcome.weights,
                "candidate_multiplier": outcome.analysis.candidate_multiplier
                if outcome.requested.value == "auto"
                else 1,
                "top_k": outcome.config.top_k,
                "sparse_top_k": outcome.config.sparse_top_k,
                "dense_top_k": outcome.config.dense_top_k,
                "rrf_k": outcome.config.rrf_k,
                "rerank": outcome.config.rerank,
                "rerank_candidates": outcome.config.rerank_candidates,
                "bm25": {"k1": settings.bm25_k1, "b": settings.bm25_b},
                "embedding_model": self.embedder.model_name,
                "reranker_model": outcome.reranker,
                "degraded": outcome.failures,
            },
            timings=outcome.timings_ms,
            latency_ms=outcome.timings_ms.get("total", 0.0),
            result_count=len(outcome.selected),
        )
        run.user = user
        run.answer = None  # an answer run gets its answer once the model replies
        results = [
            RetrievalResult(
                organization_id=member.organization_id,
                ai_run_id=run.id,
                chunk_id=candidate.chunk_id,
                document_id=chunks[candidate.chunk_id].document_id,
                document_version=chunks[candidate.chunk_id].document_version,
                chunk_index=chunks[candidate.chunk_id].chunk_index,
                sparse_rank=candidate.sparse[0] if candidate.sparse else None,
                sparse_score=candidate.sparse[1] if candidate.sparse else None,
                dense_rank=candidate.dense[0] if candidate.dense else None,
                dense_score=candidate.dense[1] if candidate.dense else None,
                fused_rank=candidate.fused_rank,
                fused_score=candidate.fused_score,
                rerank_score=candidate.rerank_score,
                final_rank=candidate.final_rank,
            )
            for candidate in outcome.candidates
            if candidate.chunk_id in chunks
        ]
        await self.runs.add(run, results)
        return run

    async def _throttle(self, user: User) -> None:
        if self.rate_limiter is None:
            return
        result = await self.rate_limiter.hit(
            f"knowledge:search:user:{user.id}",
            limit=settings.knowledge_search_limit,
            window_seconds=60,
        )
        if not result.allowed:
            raise TooManyRequestsError(
                "Too many searches. Wait a moment.", retry_after=result.retry_after
            )
