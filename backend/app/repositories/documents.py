import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import (
    AIAnswer,
    AIRun,
    AnswerStatus,
    Citation,
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentVisibility,
)

Freshness = Literal["review_due", "not_cited"]


@dataclass(frozen=True)
class CitationUsage:
    last_cited_at: datetime | None  # the latest answer that cited it, ever
    recent: int  # answers citing it since the window's start


@dataclass(frozen=True)
class UsageWindow:
    """Which answers count as recent, and from when a document is old enough to be
    judged on its citations."""

    since: datetime
    judged_before: datetime


def _with_people(stmt: Select[Any]) -> Select[Any]:
    return stmt.options(
        joinedload(Document.uploaded_by),
        joinedload(Document.owner),
        joinedload(Document.reviewed_by),
    )


def _given_answers() -> Any:
    """Citations of answers people were given: the policy let the answer through, and
    the run is a person's question, not an evaluation probe or a gap recheck."""
    return (
        select(Citation.document_id, AIRun.id.label("run_id"), AIRun.created_at)
        .join(AIAnswer, AIAnswer.ai_run_id == Citation.ai_run_id)
        .join(AIRun, AIRun.id == Citation.ai_run_id)
        .where(
            AIAnswer.status == AnswerStatus.ANSWERED,
            AIRun.config["origin"].astext.is_(None),
        )
    )


class DocumentRepository:
    """Documents and chunks; every query is scoped to one organization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, document: Document) -> None:
        self.session.add(document)

    async def delete(self, document: Document) -> None:
        await self.session.delete(document)

    async def get(
        self, organization_id: uuid.UUID, document_id: uuid.UUID, *, for_update: bool = False
    ) -> Document | None:
        stmt = _with_people(select(Document)).where(
            Document.id == document_id, Document.organization_id == organization_id
        )
        if for_update:
            stmt = stmt.with_for_update(of=Document).execution_options(populate_existing=True)
        return await self.session.scalar(stmt)

    async def get_by_sha256(self, organization_id: uuid.UUID, sha256: str) -> Document | None:
        return await self.session.scalar(
            select(Document).where(
                Document.organization_id == organization_id, Document.sha256 == sha256
            )
        )

    async def public_ids(self, organization_id: uuid.UUID) -> list[uuid.UUID]:
        """Documents customers' questions may be answered from."""
        rows = await self.session.scalars(
            select(Document.id).where(
                Document.organization_id == organization_id,
                Document.visibility == DocumentVisibility.PUBLIC,
            )
        )
        return list(rows.all())

    async def find(
        self,
        organization_id: uuid.UUID,
        *,
        statuses: Sequence[DocumentStatus],
        search: str | None,
        limit: int,
        offset: int,
        visibility: DocumentVisibility | None = None,
        freshness: Freshness | None = None,
        today: date | None = None,
        window: UsageWindow | None = None,
    ) -> tuple[list[Document], int]:
        conditions: list[ColumnElement[bool]] = [Document.organization_id == organization_id]
        if statuses:
            conditions.append(Document.status.in_(statuses))
        if visibility is not None:
            conditions.append(Document.visibility == visibility)
        if freshness == "review_due":
            assert today is not None
            conditions.append(Document.review_due_on <= today)
        elif freshness == "not_cited":
            assert window is not None
            conditions.append(self._not_cited(organization_id, window))
        if search:
            conditions.append(
                or_(
                    Document.title.icontains(search, autoescape=True),
                    Document.filename.icontains(search, autoescape=True),
                )
            )
        stmt = (
            _with_people(select(Document))
            .where(*conditions)
            .order_by(Document.updated_at.desc(), Document.id)
            .limit(limit)
            .offset(offset)
        )
        documents = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count()).select_from(Document).where(*conditions)
        )
        return documents, total or 0

    @staticmethod
    def _not_cited(organization_id: uuid.UUID, window: UsageWindow) -> ColumnElement[bool]:
        given = _given_answers().subquery()
        cited_recently = (
            select(given.c.run_id)
            .where(given.c.document_id == Document.id, given.c.created_at >= window.since)
            .exists()
        )
        return and_(
            Document.indexed_at.is_not(None),
            Document.indexed_at <= window.judged_before,
            ~cited_recently,
        )

    async def citation_usage(
        self, organization_id: uuid.UUID, document_ids: Sequence[uuid.UUID], since: datetime
    ) -> dict[uuid.UUID, CitationUsage]:
        """For each document, when an answer people were given last cited it, and how
        many such answers cited it since ``since``. Documents never cited are absent."""
        if not document_ids:
            return {}
        given = (
            _given_answers()
            .where(
                Citation.organization_id == organization_id,
                Citation.document_id.in_(document_ids),
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(
                given.c.document_id,
                func.max(given.c.created_at),
                func.count(distinct(given.c.run_id)).filter(given.c.created_at >= since),
            ).group_by(given.c.document_id)
        )
        return {
            document_id: CitationUsage(last_cited_at=last, recent=recent)
            for document_id, last, recent in rows
        }

    async def chunks(
        self, document_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[DocumentChunk], int]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
            .limit(limit)
            .offset(offset)
        )
        chunks = list((await self.session.scalars(stmt)).all())
        total = await self.session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
        )
        return chunks, total or 0

    async def chunks_by_id(
        self, organization_id: uuid.UUID, chunk_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, DocumentChunk]:
        """Chunk rows (with their document) for vector-search hits. Ids with no row
        (replaced or deleted since they were indexed) are simply absent."""
        if not chunk_ids:
            return {}
        rows = await self.session.scalars(
            select(DocumentChunk)
            .options(joinedload(DocumentChunk.document))
            .where(
                DocumentChunk.organization_id == organization_id,
                DocumentChunk.id.in_(chunk_ids),
            )
        )
        return {chunk.id: chunk for chunk in rows}
