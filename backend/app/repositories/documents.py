import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Document, DocumentChunk, DocumentStatus


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
        stmt = (
            select(Document)
            .options(joinedload(Document.uploaded_by))
            .where(Document.id == document_id, Document.organization_id == organization_id)
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

    async def find(
        self,
        organization_id: uuid.UUID,
        *,
        statuses: Sequence[DocumentStatus],
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Document], int]:
        conditions: list[ColumnElement[bool]] = [Document.organization_id == organization_id]
        if statuses:
            conditions.append(Document.status.in_(statuses))
        if search:
            conditions.append(
                or_(
                    Document.title.icontains(search, autoescape=True),
                    Document.filename.icontains(search, autoescape=True),
                )
            )
        stmt = (
            select(Document)
            .options(joinedload(Document.uploaded_by))
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
