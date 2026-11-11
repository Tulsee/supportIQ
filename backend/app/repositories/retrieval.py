"""Retrieval data access: BM25 over PostgreSQL full-text search, chunk texts for
reranking, and recorded runs."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ai_engine.retrieval.fusion import RankedChunk
from app.models import AIRun, DocumentChunk, RetrievalResult

# Okapi BM25, computed per organization (its own corpus statistics, so tenants
# never influence each other's scores):
#
#   score(d) = sum over query terms t in d of
#              idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
#   idf(t)   = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
#
# Terms come from the same English analyzer that built ``search_vector`` (stemming,
# stopwords), tf from the tsvector's positions, |d| is the chunk's token count. The
# GIN index on ``search_vector`` finds the candidates; every chunk containing any
# query term is a candidate, so df counts over the candidates are exact.
_BM25 = text(
    """
    WITH terms AS (
        SELECT DISTINCT term
        FROM unnest(tsvector_to_array(to_tsvector('english'::regconfig, :query))) AS term
    ),
    corpus AS (
        SELECT count(*)::float8 AS n, coalesce(avg(token_count), 1)::float8 AS avgdl
        FROM document_chunks
        WHERE organization_id = :organization_id
    ),
    matches AS (
        SELECT c.id, c.token_count, e.lexeme AS term,
               coalesce(array_length(e.positions, 1), 1)::float8 AS tf
        FROM document_chunks AS c
        CROSS JOIN LATERAL unnest(c.search_vector) AS e(lexeme, positions, weights)
        JOIN terms AS t ON t.term = e.lexeme
        WHERE c.organization_id = :organization_id
          AND c.search_vector @@ (
              SELECT to_tsquery('simple', string_agg(quote_literal(term), ' | ')) FROM terms
          )
    ),
    df AS (
        SELECT term, count(*)::float8 AS df FROM matches GROUP BY term
    ),
    -- Typed explicitly: an untyped parameter next to an integer literal would be
    -- inferred as an integer (k1 = 1.2 silently becoming 1).
    params AS (
        SELECT CAST(:k1 AS float8) AS k1, CAST(:b AS float8) AS b
    )
    SELECT m.id,
           sum(
               ln(1 + (corpus.n - df.df + 0.5) / (df.df + 0.5))
               * m.tf * (params.k1 + 1)
               / (m.tf + params.k1 * (1 - params.b + params.b * m.token_count / corpus.avgdl))
           ) AS score
    FROM matches AS m
    JOIN df USING (term)
    CROSS JOIN corpus
    CROSS JOIN params
    GROUP BY m.id
    ORDER BY score DESC, m.id
    LIMIT :limit
    """
)


class BM25Retriever:
    """Keyword retrieval within one organization."""

    def __init__(
        self, session: AsyncSession, organization_id: uuid.UUID, *, k1: float, b: float
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.k1 = k1
        self.b = b

    async def search(self, query: str, limit: int) -> list[RankedChunk]:
        rows = await self.session.execute(
            _BM25,
            {
                "query": query,
                "organization_id": self.organization_id,
                "k1": self.k1,
                "b": self.b,
                "limit": limit,
            },
        )
        return [RankedChunk(row.id, float(row.score)) for row in rows]


class ChunkPassages:
    """Chunk texts for reranking; ids of replaced or deleted chunks are absent."""

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id

    async def load(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not chunk_ids:
            return {}
        rows = await self.session.execute(
            select(DocumentChunk.id, DocumentChunk.content).where(
                DocumentChunk.organization_id == self.organization_id,
                DocumentChunk.id.in_(chunk_ids),
            )
        )
        return {row.id: row.content for row in rows}


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, run: AIRun, results: Sequence[RetrievalResult]) -> None:
        self.session.add(run)
        await self.session.flush()  # the run row first: results reference it
        self.session.add_all(results)

    async def get(self, organization_id: uuid.UUID, run_id: uuid.UUID) -> AIRun | None:
        return await self.session.scalar(
            select(AIRun)
            .options(joinedload(AIRun.user))
            .where(AIRun.id == run_id, AIRun.organization_id == organization_id)
        )

    async def results(self, run_id: uuid.UUID) -> list[RetrievalResult]:
        """Selected passages first (by final rank), then the other candidates."""
        rows = await self.session.scalars(
            select(RetrievalResult)
            .options(
                joinedload(RetrievalResult.chunk),
                joinedload(RetrievalResult.document),
            )
            .where(RetrievalResult.ai_run_id == run_id)
            .order_by(RetrievalResult.final_rank.asc().nulls_last(), RetrievalResult.fused_rank)
        )
        return list(rows.all())

    async def find(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[AIRun], int]:
        condition = AIRun.organization_id == organization_id
        runs = await self.session.scalars(
            select(AIRun)
            .options(joinedload(AIRun.user))
            .where(condition)
            .order_by(AIRun.created_at.desc(), AIRun.id)
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(select(func.count()).select_from(AIRun).where(condition))
        return list(runs.all()), total or 0
