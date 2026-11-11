"""The ingestion pipeline: original file -> text -> chunks -> embeddings -> index.

Runs in a background worker. Each attempt is identified by the document's
``ingestion_id``; a job for any other id is obsolete (a retry or a new version
replaced it) and exits without doing anything. Re-delivery of the same job is
harmless: the swap below is all-or-nothing.

Consistency: PostgreSQL is the source of truth for what is searchable. Vectors for
the new chunks are written first; then one transaction replaces the document's
chunk rows and marks it indexed; then the previous run's vectors are deleted.
Search resolves vector hits against chunk rows, so at every moment results reflect
exactly one complete version of each document: never a mix, never stale chunks.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from functools import partial

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_engine.chunking.chunker import Chunk, ChunkingConfig, chunk_blocks
from ai_engine.embeddings.embedder import Embedder
from ai_engine.ingestion.extract import ExtractionError, extract
from ai_engine.retrieval.vector_store import ChunkPoint, QdrantVectorStore
from app.core.config import settings
from app.core.database import rls_bypass
from app.core.security import utcnow
from app.core.storage import ObjectStorage, StorageError
from app.models import ACTIVE_INGESTION, Document, DocumentChunk, DocumentStatus

logger = logging.getLogger(__name__)

EMBED_BATCH = 64


class Outcome(StrEnum):
    INDEXED = "indexed"
    FAILED = "failed"
    SKIPPED = "skipped"  # obsolete job: another attempt or version replaced it


class TransientIngestionError(Exception):
    """A dependency failed (storage, Qdrant, database); retrying may succeed."""


@dataclass(frozen=True)
class IngestionLimits:
    max_pages: int
    max_characters: int
    chunking: ChunkingConfig

    @classmethod
    def from_settings(cls) -> "IngestionLimits":
        return cls(
            max_pages=settings.max_document_pages,
            max_characters=settings.max_extracted_characters,
            chunking=ChunkingConfig(settings.chunk_size_tokens, settings.chunk_overlap_tokens),
        )


def storage_prefix(document: Document) -> str:
    return f"documents/{document.organization_id}/{document.id}/"


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        embedder: Embedder,
        vector_store: QdrantVectorStore,
        limits: IngestionLimits,
    ) -> None:
        self.session = session
        self.storage = storage
        self.embedder = embedder
        self.vectors = vector_store
        self.limits = limits

    async def run(
        self, document_id: uuid.UUID, ingestion_id: uuid.UUID, *, final_attempt: bool = True
    ) -> Outcome:
        """Process one ingestion attempt. Permanent problems (unreadable file) mark
        the document failed. Transient ones raise TransientIngestionError for the
        job runner to retry, or mark it failed on the final attempt."""
        # A system operation without a user: scoped in code to this one document.
        async with rls_bypass(self.session):
            document = await self._claim(document_id, ingestion_id)
            if document is None:
                return Outcome.SKIPPED
            try:
                return await self._process(document, ingestion_id)
            except ExtractionError as exc:
                await self._fail(document_id, ingestion_id, str(exc))
                return Outcome.FAILED
            except Exception as exc:
                await self.session.rollback()
                await self._discard_vectors(ingestion_id)
                if not final_attempt:
                    logger.warning("Ingestion of %s will be retried: %s", document_id, exc)
                    raise TransientIngestionError(str(exc)) from exc
                logger.exception("Ingestion of %s failed", document_id)
                await self._fail(
                    document_id,
                    ingestion_id,
                    "Indexing failed because a service was unavailable. Try again.",
                )
                return Outcome.FAILED

    # --- Steps ---------------------------------------------------------------------

    async def _claim(self, document_id: uuid.UUID, ingestion_id: uuid.UUID) -> Document | None:
        document = await self._current(document_id, ingestion_id)
        if document is None:
            return None
        document.status = DocumentStatus.PROCESSING
        document.processing_started_at = utcnow()
        document.error = None
        await self.session.commit()
        return document

    async def _current(self, document_id: uuid.UUID, ingestion_id: uuid.UUID) -> Document | None:
        """The document, locked, if this attempt is still the one to run."""
        document = await self.session.scalar(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            # Refresh from the locked row: the object may already be in the session
            # with values from before another request (a retry, a new version) ran.
            .execution_options(populate_existing=True)
        )
        if document is None or document.ingestion_id != ingestion_id:
            return None
        if document.status not in ACTIVE_INGESTION:
            return None
        return document

    async def _process(self, document: Document, ingestion_id: uuid.UUID) -> Outcome:
        try:
            content = await self.storage.get(document.storage_key)
        except StorageError as exc:
            raise TransientIngestionError(f"Couldn't read the original file: {exc}") from exc
        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise ExtractionError("The stored file doesn't match the upload. Upload it again.")

        extracted = await to_thread.run_sync(
            partial(
                extract,
                document.file_type,
                content,
                max_pages=self.limits.max_pages,
                max_characters=self.limits.max_characters,
            )
        )
        counter = await to_thread.run_sync(self.embedder.token_counter)
        chunks = await to_thread.run_sync(
            chunk_blocks, extracted.blocks, counter, self.limits.chunking
        )
        if not chunks:
            raise ExtractionError("The document contains no text.")
        vectors = await self._embed(chunks)

        chunk_ids = [uuid.uuid4() for _ in chunks]
        await self.vectors.ensure_collection()
        await self.vectors.upsert(
            [
                ChunkPoint(
                    chunk_id=chunk_id,
                    vector=vector,
                    organization_id=document.organization_id,
                    document_id=document.id,
                    document_version=document.version,
                    ingestion_id=ingestion_id,
                    chunk_index=chunk.index,
                    page=chunk.page,
                )
                for chunk_id, chunk, vector in zip(chunk_ids, chunks, vectors, strict=True)
            ]
        )

        # The swap: all of this run's chunks replace all previous ones, atomically.
        document = await self._current(document.id, ingestion_id)
        if document is None:  # deleted, retried, or re-versioned while we worked
            await self.session.rollback()
            await self._discard_vectors(ingestion_id)
            return Outcome.SKIPPED
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
        self.session.add_all(
            DocumentChunk(
                id=chunk_id,
                organization_id=document.organization_id,
                document_id=document.id,
                document_version=document.version,
                ingestion_id=ingestion_id,
                chunk_index=chunk.index,
                page_number=chunk.page,
                page_end=chunk.page_end,
                section_heading=chunk.heading,
                start_offset=chunk.start,
                end_offset=chunk.end,
                content=chunk.text,
                token_count=chunk.token_count,
                embedding_model=self.embedder.model_name,
            )
            for chunk_id, chunk in zip(chunk_ids, chunks, strict=True)
        )
        document.status = DocumentStatus.INDEXED
        document.indexed_version = document.version
        document.page_count = extracted.page_count
        document.chunk_count = len(chunks)
        document.token_count = sum(chunk.token_count for chunk in chunks)
        document.embedding_model = self.embedder.model_name
        document.indexed_at = utcnow()
        document.error = "\n".join(extracted.warnings) or None
        current_key = document.storage_key
        prefix = storage_prefix(document)
        await self.session.commit()

        await self._cleanup_after_swap(document.id, ingestion_id, prefix, current_key)
        logger.info(
            "Indexed document %s v%s: %s chunks", document.id, document.version, len(chunks)
        )
        return Outcome.INDEXED

    async def _embed(self, chunks: list[Chunk]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            texts = [chunk.text for chunk in chunks[i : i + EMBED_BATCH]]
            vectors.extend(await to_thread.run_sync(self.embedder.embed_documents, texts))
        return vectors

    async def _cleanup_after_swap(
        self, document_id: uuid.UUID, ingestion_id: uuid.UUID, prefix: str, current_key: str
    ) -> None:
        """Previous runs' vectors and superseded original files. Best effort: they
        are no longer reachable through search either way."""
        try:
            await self.vectors.delete_document(document_id, except_ingestion=ingestion_id)
        except Exception as exc:
            logger.warning("Couldn't delete old vectors of %s: %s", document_id, exc)
        try:
            for key in await self.storage.list_keys(prefix):
                if key != current_key:
                    await self.storage.delete(key)
        except StorageError as exc:
            logger.warning("Couldn't delete superseded files of %s: %s", document_id, exc)

    async def _discard_vectors(self, ingestion_id: uuid.UUID) -> None:
        try:
            await self.vectors.delete_ingestion(ingestion_id)
        except Exception as exc:
            logger.warning("Couldn't discard vectors of ingestion %s: %s", ingestion_id, exc)

    async def _fail(self, document_id: uuid.UUID, ingestion_id: uuid.UUID, message: str) -> None:
        await self.session.rollback()
        document = await self._current(document_id, ingestion_id)
        if document is not None:
            document.status = DocumentStatus.FAILED
            document.error = message
            await self.session.commit()


async def run_ingestion_job(
    document_id: uuid.UUID, ingestion_id: uuid.UUID, *, final_attempt: bool
) -> Outcome:
    """Entry point for a worker: its own event loop, database engine, and Qdrant
    client per job (nothing bound to another loop is reused)."""
    from app.core.knowledge import get_embedder, new_qdrant_client, vector_store_for
    from app.core.storage import get_object_storage

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    client = new_qdrant_client()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            embedder = get_embedder()  # process-wide: the model loads once
            service = IngestionService(
                session,
                get_object_storage(),
                embedder,
                vector_store_for(client, embedder),
                IngestionLimits.from_settings(),
            )
            return await service.run(document_id, ingestion_id, final_attempt=final_attempt)
    finally:
        await client.close()
        await engine.dispose()
