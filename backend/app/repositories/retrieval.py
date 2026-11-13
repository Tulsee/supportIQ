"""Retrieval data access: BM25 over PostgreSQL full-text search, chunk texts for
reranking, and recorded runs."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only, selectinload

from ai_engine.retrieval.fusion import RankedChunk
from app.models import (
    AIAnswer,
    AIRun,
    AIRunKind,
    Citation,
    Document,
    DocumentChunk,
    DocumentVisibility,
    RetrievalResult,
)

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
#
# ``public_only`` (the customer assistant, Module 12) restricts the corpus itself to
# public documents, statistics included, so scores reveal nothing about internal
# chunks. Row-level security enforces the same for a customer's session.
_BM25 = text(
    """
    -- Typed explicitly: an untyped parameter next to an integer literal would be
    -- inferred as an integer (k1 = 1.2 silently becoming 1).
    WITH params AS (
        SELECT CAST(:k1 AS float8) AS k1, CAST(:b AS float8) AS b,
               CAST(:public_only AS boolean) AS public_only
    ),
    public_documents AS (
        SELECT id FROM documents
        WHERE organization_id = :organization_id AND visibility = 'public'
    ),
    terms AS (
        SELECT DISTINCT term
        FROM unnest(tsvector_to_array(to_tsvector('english'::regconfig, :query))) AS term
    ),
    corpus AS (
        SELECT count(*)::float8 AS n, coalesce(avg(token_count), 1)::float8 AS avgdl
        FROM document_chunks
        WHERE organization_id = :organization_id
          AND (NOT (SELECT public_only FROM params)
               OR document_id IN (SELECT id FROM public_documents))
    ),
    matches AS (
        SELECT c.id, c.token_count, e.lexeme AS term,
               coalesce(array_length(e.positions, 1), 1)::float8 AS tf
        FROM document_chunks AS c
        CROSS JOIN LATERAL unnest(c.search_vector) AS e(lexeme, positions, weights)
        JOIN terms AS t ON t.term = e.lexeme
        WHERE c.organization_id = :organization_id
          AND (NOT (SELECT public_only FROM params)
               OR c.document_id IN (SELECT id FROM public_documents))
          AND c.search_vector @@ (
              SELECT to_tsquery('simple', string_agg(quote_literal(term), ' | ')) FROM terms
          )
    ),
    df AS (
        SELECT term, count(*)::float8 AS df FROM matches GROUP BY term
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
    """Keyword retrieval within one organization (``public_only``: its public documents)."""

    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        k1: float,
        b: float,
        public_only: bool = False,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.k1 = k1
        self.b = b
        self.public_only = public_only

    async def search(self, query: str, limit: int) -> list[RankedChunk]:
        rows = await self.session.execute(
            _BM25,
            {
                "query": query,
                "organization_id": self.organization_id,
                "k1": self.k1,
                "b": self.b,
                "public_only": self.public_only,
                "limit": limit,
            },
        )
        return [RankedChunk(row.id, float(row.score)) for row in rows]


class ChunkPassages:
    """Chunk texts for reranking; ids of replaced or deleted chunks are absent, and
    with ``public_only`` so are chunks of internal documents."""

    def __init__(
        self, session: AsyncSession, organization_id: uuid.UUID, *, public_only: bool = False
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.public_only = public_only

    async def load(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not chunk_ids:
            return {}
        stmt = select(DocumentChunk.id, DocumentChunk.content).where(
            DocumentChunk.organization_id == self.organization_id,
            DocumentChunk.id.in_(chunk_ids),
        )
        if self.public_only:
            stmt = stmt.join(Document, Document.id == DocumentChunk.document_id).where(
                Document.visibility == DocumentVisibility.PUBLIC
            )
        rows = await self.session.execute(stmt)
        return {row.id: row.content for row in rows}


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, run: AIRun, results: Sequence[RetrievalResult]) -> None:
        self.session.add(run)
        await self.session.flush()  # the run row first: results reference it
        self.session.add_all(results)

    async def add_answer(self, answer: AIAnswer, citations: Sequence[Citation]) -> None:
        self.session.add(answer)
        await self.session.flush()  # citations reference the answer
        self.session.add_all(citations)

    async def get(self, organization_id: uuid.UUID, run_id: uuid.UUID) -> AIRun | None:
        """The run with its user, and its answer and citations if it has one."""
        return await self.session.scalar(
            select(AIRun)
            .options(
                joinedload(AIRun.user),
                selectinload(AIRun.answer)
                .selectinload(AIAnswer.citations)
                .joinedload(Citation.document),
                selectinload(AIRun.assessment),
            )
            .where(AIRun.id == run_id, AIRun.organization_id == organization_id)
            .execution_options(populate_existing=True)
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
        self,
        organization_id: uuid.UUID,
        *,
        kind: AIRunKind | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[AIRun], int]:
        condition = AIRun.organization_id == organization_id
        if kind is not None:
            condition &= AIRun.kind == kind
        runs = await self.session.scalars(
            select(AIRun)
            .options(
                joinedload(AIRun.user),
                # Only the status for the list, not the prompts and texts.
                joinedload(AIRun.answer).options(load_only(AIAnswer.status)),
            )
            .where(condition)
            .order_by(AIRun.created_at.desc(), AIRun.id)
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(select(func.count()).select_from(AIRun).where(condition))
        return list(runs.all()), total or 0
