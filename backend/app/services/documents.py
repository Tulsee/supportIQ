"""Knowledge-base documents: upload, versions, re-indexing, deletion, search.

Admins manage documents; admins and agents can read and search them. Uploads are
validated synchronously (type confirmed from content, size, duplicates), stored
in object storage, and indexed asynchronously by a worker.
"""

import hashlib
import logging
import re
import uuid
from collections.abc import Sequence
from functools import partial
from pathlib import PurePath, PureWindowsPath

from anyio import to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.ingestion.extract import MEDIA_TYPES, DocumentType, ExtractionError, detect_type
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    InvalidFileError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
)
from app.core.rate_limit import RateLimiter
from app.core.storage import ObjectStorage, StorageError
from app.models import (
    ACTIVE_INGESTION,
    Document,
    DocumentChunk,
    DocumentStatus,
    OrganizationMember,
    OrgRole,
    User,
)
from app.repositories.documents import DocumentRepository
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.services.ingestion import storage_prefix
from app.workers.queue import IngestionQueue, QueueUnavailableError

logger = logging.getLogger(__name__)

ALREADY_INDEXING = "This document is already being indexed. Wait until it finishes."
STORAGE_DOWN = "File storage is unavailable right now. Try again shortly."

_UNSAFE_FILENAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def safe_filename(name: str) -> str:
    """The base name only (no directories from either path style), without
    characters that are unsafe in file systems or headers."""
    base = PureWindowsPath(PurePath(name).name).name
    cleaned = _UNSAFE_FILENAME.sub("_", base).strip(" .") or "document"
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and len(cleaned) > 255:
        return f"{stem[: 254 - len(suffix)]}.{suffix}"
    return cleaned[:255]


def default_title(filename: str) -> str:
    """``employee-handbook.docx`` -> ``Employee handbook``."""
    stem = " ".join(PurePath(filename).stem.replace("_", " ").replace("-", " ").split())
    title = stem or filename
    return (title[:1].upper() + title[1:])[:300]


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: ObjectStorage | None = None,
        queue: IngestionQueue | None = None,
        rate_limiter: RateLimiter | None = None,
        vector_store: QdrantVectorStore | None = None,
    ) -> None:
        self.session = session
        self.documents = DocumentRepository(session)
        self.storage = storage
        self.queue = queue
        self.rate_limiter = rate_limiter
        self.vectors = vector_store

    # --- Reading --------------------------------------------------------------------

    async def list_documents(
        self,
        member: OrganizationMember,
        *,
        statuses: Sequence[DocumentStatus],
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Document], int]:
        require_role(member, *STAFF_ROLES)
        return await self.documents.find(
            member.organization_id, statuses=statuses, search=search, limit=limit, offset=offset
        )

    async def get(self, member: OrganizationMember, document_id: uuid.UUID) -> Document:
        require_role(member, *STAFF_ROLES)
        document = await self.documents.get(member.organization_id, document_id)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def list_chunks(
        self, member: OrganizationMember, document_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[DocumentChunk], int]:
        document = await self.get(member, document_id)
        return await self.documents.chunks(document.id, limit=limit, offset=offset)

    async def read_original(
        self, member: OrganizationMember, document_id: uuid.UUID
    ) -> tuple[Document, bytes]:
        document = await self.get(member, document_id)
        assert self.storage is not None
        try:
            return document, await self.storage.get(document.storage_key)
        except StorageError as exc:
            raise ServiceUnavailableError(STORAGE_DOWN) from exc

    # --- Writing --------------------------------------------------------------------

    async def upload(
        self,
        member: OrganizationMember,
        user: User,
        *,
        filename: str,
        content: bytes,
        title: str | None,
    ) -> Document:
        require_role(member, OrgRole.ADMIN)
        await self._throttle(user)
        name, kind, sha256 = await self._validate(filename, content)
        await self._ensure_unique(member.organization_id, sha256)

        document = Document(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            title=title or default_title(name),
            filename=name,
            file_type=kind,
            size_bytes=len(content),
            sha256=sha256,
            version=1,
            status=DocumentStatus.QUEUED,
            ingestion_id=uuid.uuid4(),
            uploaded_by_id=user.id,
            chunk_count=0,
            token_count=0,
        )
        document.uploaded_by = user
        document.storage_key = self._new_key(document, name)
        await self._store(document.storage_key, content, kind)
        self.documents.add(document)
        await self._commit_or_discard(document.storage_key)
        return document

    async def add_version(
        self,
        member: OrganizationMember,
        user: User,
        document_id: uuid.UUID,
        *,
        filename: str,
        content: bytes,
    ) -> Document:
        """Replace the file. The previous version stays searchable until the new one
        is indexed (or keeps being searched if the new one fails)."""
        require_role(member, OrgRole.ADMIN)
        await self._throttle(user)
        document = await self._get_for_update(member, document_id)
        if document.status in ACTIVE_INGESTION:
            raise ConflictError(ALREADY_INDEXING, code="ingestion_in_progress")
        name, kind, sha256 = await self._validate(filename, content)
        if sha256 == document.sha256:
            raise ConflictError("This file is identical to the current version.", code="duplicate")
        await self._ensure_unique(member.organization_id, sha256)

        document.version += 1
        document.filename = name
        document.file_type = kind
        document.size_bytes = len(content)
        document.sha256 = sha256
        document.storage_key = self._new_key(document, name)
        self._requeue(document)
        await self._store(document.storage_key, content, kind)
        await self._commit_or_discard(document.storage_key)
        return document

    async def reindex(self, member: OrganizationMember, document_id: uuid.UUID) -> Document:
        """Process the current version again (after a failure, or a new chunking or
        embedding configuration)."""
        require_role(member, OrgRole.ADMIN)
        document = await self._get_for_update(member, document_id)
        if document.status in ACTIVE_INGESTION:
            raise ConflictError(ALREADY_INDEXING, code="ingestion_in_progress")
        self._requeue(document)
        await self.session.commit()
        return document

    async def start_ingestion(self, document: Document) -> Document:
        """Hand the current attempt to a worker (after the document is committed)."""
        assert self.queue is not None
        try:
            await self.queue.enqueue(document.id, document.ingestion_id)
        except QueueUnavailableError:
            logger.exception("Couldn't queue ingestion of %s", document.id)
            fresh = await self.documents.get(document.organization_id, document.id, for_update=True)
            if fresh is not None and fresh.ingestion_id == document.ingestion_id:
                fresh.status = DocumentStatus.FAILED
                fresh.error = (
                    "Indexing couldn't be started because the job queue is unavailable. Try again."
                )
                await self.session.commit()
                return fresh
        return document

    async def delete(self, member: OrganizationMember, document_id: uuid.UUID) -> None:
        require_role(member, OrgRole.ADMIN)
        document = await self._get_for_update(member, document_id)
        prefix = storage_prefix(document)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="document",
            entity_id=document.id,
            action="document.deleted",
            summary=f"Deleted the document {document.title} ({document.filename})",
            changes={"version": {"from": document.version, "to": None}},
        )
        await self.documents.delete(document)  # chunk rows cascade
        await self.session.commit()
        # Unreachable through search already (no chunk rows); clean up best effort.
        if self.vectors is not None:
            try:
                await self.vectors.delete_document(document_id)
            except Exception as exc:
                logger.warning("Couldn't delete vectors of %s: %s", document_id, exc)
        if self.storage is not None:
            try:
                for key in await self.storage.list_keys(prefix):
                    await self.storage.delete(key)
            except StorageError as exc:
                logger.warning("Couldn't delete files of %s: %s", document_id, exc)

    # --- Helpers --------------------------------------------------------------------

    async def _throttle(self, user: User) -> None:
        if self.rate_limiter is None:
            return
        result = await self.rate_limiter.hit(
            f"documents:upload:user:{user.id}",
            limit=settings.document_upload_limit,
            window_seconds=60 * 60,
        )
        if not result.allowed:
            raise TooManyRequestsError(
                "Too many uploads in the last hour. Try again later.",
                retry_after=result.retry_after,
            )

    async def _validate(self, filename: str, content: bytes) -> tuple[str, DocumentType, str]:
        name = safe_filename(filename)
        try:
            kind = await to_thread.run_sync(partial(detect_type, name, content))
        except ExtractionError as exc:
            raise InvalidFileError(str(exc)) from exc
        return name, kind, hashlib.sha256(content).hexdigest()

    async def _ensure_unique(self, organization_id: uuid.UUID, sha256: str) -> None:
        existing = await self.documents.get_by_sha256(organization_id, sha256)
        if existing is not None:
            raise ConflictError(
                f"This file is already in the knowledge base as “{existing.title}”.",
                code="duplicate",
                details={"document_id": str(existing.id)},
            )

    async def _get_for_update(self, member: OrganizationMember, document_id: uuid.UUID) -> Document:
        document = await self.documents.get(member.organization_id, document_id, for_update=True)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    @staticmethod
    def _new_key(document: Document, filename: str) -> str:
        # Generated, never user-controlled: the original name is only metadata.
        suffix = PurePath(filename).suffix.lower()
        return f"{storage_prefix(document)}v{document.version}/{uuid.uuid4().hex}{suffix}"

    @staticmethod
    def _requeue(document: Document) -> None:
        document.status = DocumentStatus.QUEUED
        document.ingestion_id = uuid.uuid4()  # any earlier job for this document is void
        document.error = None
        document.processing_started_at = None

    async def _store(self, key: str, content: bytes, kind: DocumentType) -> None:
        assert self.storage is not None
        try:
            await self.storage.put(key, content, MEDIA_TYPES[kind])
        except StorageError as exc:
            await self.session.rollback()
            raise ServiceUnavailableError(STORAGE_DOWN) from exc

    async def _commit_or_discard(self, key: str) -> None:
        """Commit; if that fails, remove the file stored for it."""
        try:
            await self.session.commit()
        except IntegrityError as exc:  # lost a race with an identical upload
            await self.session.rollback()
            await self._discard(key)
            raise ConflictError(
                "This file is already in the knowledge base.", code="duplicate"
            ) from exc
        except BaseException:
            await self._discard(key)
            raise

    async def _discard(self, key: str) -> None:
        assert self.storage is not None
        try:
            await self.storage.delete(key)
        except StorageError:
            logger.warning("Couldn't remove orphaned upload %s", key)
