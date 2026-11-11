"""Knowledge-base documents of the organization in ``X-Organization-Id``.

Admins upload, version, re-index, and delete documents; admins and agents list,
inspect, download, and search them. Indexing is asynchronous: an upload returns
``202 Accepted`` with status ``queued``; poll the document until it is
``indexed`` or ``failed``.
"""

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from ai_engine.ingestion.extract import MEDIA_TYPES
from app.api.deps import (
    CurrentUser,
    DbSession,
    EmbedderDep,
    IngestionQueueDep,
    Pagination,
    RateLimiterDep,
    StorageDep,
    TenantMembership,
    VectorStoreDep,
    WorkerRegistryDep,
)
from app.core.config import settings
from app.core.exceptions import PayloadTooLargeError
from app.models import DocumentStatus
from app.schemas.common import Page, error_responses
from app.schemas.documents import (
    ChunkRead,
    DocumentRead,
    DocumentSummary,
    DocumentTitle,
    SearchHit,
    SearchResults,
    WorkerStatus,
)
from app.services.authorization import STAFF_ROLES, require_role
from app.services.documents import DocumentService, KnowledgeSearchService

router = APIRouter(
    prefix="/documents",
    tags=["knowledge base"],
    responses=error_responses(401, 403, 404, 409, 413, 422, 429, 503),
)

# Multipart framing around the file; anything beyond is rejected before parsing.
_MULTIPART_OVERHEAD = 64 * 1024
_TOO_LARGE = f"Files can be at most {settings.max_upload_bytes // (1024 * 1024)} MB."


def reject_oversized_requests(request: Request) -> None:
    """Refuse large bodies from their declared length, before they are read."""
    declared = request.headers.get("content-length")
    if (
        declared
        and declared.isdigit()
        and int(declared) > settings.max_upload_bytes + _MULTIPART_OVERHEAD
    ):
        raise PayloadTooLargeError(_TOO_LARGE)


async def read_upload(file: UploadFile) -> bytes:
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise PayloadTooLargeError(_TOO_LARGE)
    return content


UploadedFile = Annotated[UploadFile, File(description="PDF, DOCX, TXT, or Markdown.")]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document (admin)",
    dependencies=[Depends(reject_oversized_requests)],
)
async def upload_document(
    file: UploadedFile,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    storage: StorageDep,
    queue: IngestionQueueDep,
    limiter: RateLimiterDep,
    title: Annotated[DocumentTitle | None, Form()] = None,
) -> DocumentRead:
    """Validates the file (type confirmed from its content, size, duplicates),
    stores it, and queues indexing. 409 `duplicate` if the same file exists."""
    service = DocumentService(session, storage=storage, queue=queue, rate_limiter=limiter)
    document = await service.upload(
        member,
        user,
        filename=file.filename or "document",
        content=await read_upload(file),
        title=title,
    )
    return DocumentRead.build(await service.start_ingestion(document))


@router.get("", summary="List documents")
async def list_documents(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    status_: Annotated[list[DocumentStatus] | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100, description="Title or file name.")] = None,
) -> Page[DocumentRead]:
    documents, total = await DocumentService(session).list_documents(
        member, statuses=status_ or (), search=search, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[DocumentRead.build(d) for d in documents],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/workers", summary="Is a worker running to index documents?")
async def worker_status(member: TenantMembership, registry: WorkerRegistryDep) -> WorkerStatus:
    """Without a running worker, uploads wait in the queue (status `queued`)."""
    require_role(member, *STAFF_ROLES)
    workers = await registry.live_workers()
    if workers is None:
        return WorkerStatus(status="unknown", workers=None)
    return WorkerStatus(status="online" if workers else "offline", workers=workers)


@router.get("/search", summary="Search the knowledge base")
async def search_documents(
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    embedder: EmbedderDep,
    vector_store: VectorStoreDep,
    limiter: RateLimiterDep,
    q: Annotated[str, Query(min_length=2, max_length=500, description="What to look for.")],
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SearchResults:
    """Semantic (dense vector) search over indexed chunks, most similar first."""
    hits = await KnowledgeSearchService(session, embedder, vector_store, limiter).search(
        member, user, q.strip(), limit=limit
    )
    return SearchResults(
        query=q,
        embedding_model=embedder.model_name,
        results=[
            SearchHit(
                score=score,
                document=DocumentSummary(
                    id=chunk.document.id,
                    title=chunk.document.title,
                    file_type=chunk.document.file_type,
                ),
                chunk=ChunkRead.build(chunk),
            )
            for chunk, score in hits
        ],
    )


@router.get("/{document_id}", summary="Get a document")
async def get_document(
    document_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> DocumentRead:
    return DocumentRead.build(await DocumentService(session).get(member, document_id))


@router.get("/{document_id}/chunks", summary="List a document's indexed chunks")
async def list_chunks(
    document_id: uuid.UUID, member: TenantMembership, session: DbSession, page: Pagination
) -> Page[ChunkRead]:
    chunks, total = await DocumentService(session).list_chunks(
        member, document_id, limit=page.limit, offset=page.offset
    )
    return Page(
        items=[ChunkRead.build(c) for c in chunks],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{document_id}/file",
    summary="Download the original file",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
)
async def download_document(
    document_id: uuid.UUID, member: TenantMembership, session: DbSession, storage: StorageDep
) -> Response:
    document, content = await DocumentService(session, storage=storage).read_original(
        member, document_id
    )
    ascii_name = document.filename.encode("ascii", "replace").decode().replace('"', "_")
    return Response(
        content,
        media_type=MEDIA_TYPES[document.file_type],
        headers={
            # Always a download, never rendered in the app's origin.
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(document.filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/{document_id}/versions",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a new version (admin)",
    dependencies=[Depends(reject_oversized_requests)],
)
async def upload_version(
    document_id: uuid.UUID,
    file: UploadedFile,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    storage: StorageDep,
    queue: IngestionQueueDep,
    limiter: RateLimiterDep,
) -> DocumentRead:
    """The previous version stays searchable until the new one is indexed."""
    service = DocumentService(session, storage=storage, queue=queue, rate_limiter=limiter)
    document = await service.add_version(
        member,
        user,
        document_id,
        filename=file.filename or "document",
        content=await read_upload(file),
    )
    return DocumentRead.build(await service.start_ingestion(document))


@router.post(
    "/{document_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index the current version again (admin)",
)
async def reindex_document(
    document_id: uuid.UUID, member: TenantMembership, session: DbSession, queue: IngestionQueueDep
) -> DocumentRead:
    """Retry a failed document, or re-process one after configuration changes.
    409 `ingestion_in_progress` while it is queued or processing."""
    service = DocumentService(session, queue=queue)
    document = await service.reindex(member, document_id)
    return DocumentRead.build(await service.start_ingestion(document))


@router.delete(
    "/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a document (admin)"
)
async def delete_document(
    document_id: uuid.UUID,
    member: TenantMembership,
    session: DbSession,
    storage: StorageDep,
    vector_store: VectorStoreDep,
) -> None:
    await DocumentService(session, storage=storage, vector_store=vector_store).delete(
        member, document_id
    )
