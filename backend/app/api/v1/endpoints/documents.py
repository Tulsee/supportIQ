"""Knowledge-base documents of the organization in ``X-Organization-Id``.

Admins upload, version, re-index, rename, publish (``visibility``) and delete
documents, and give them an owner and a review date; admins and agents list, inspect,
download, and search them, and a document's owner marks it reviewed. Indexing is
asynchronous: an upload returns ``202 Accepted`` with status ``queued``; poll the
document until it is ``indexed`` or ``failed``.
"""

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from ai_engine.ingestion.extract import MEDIA_TYPES
from app.api.deps import (
    CurrentUser,
    DbSession,
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
from app.models import DocumentStatus, DocumentVisibility
from app.repositories.documents import Freshness
from app.schemas.common import Page, error_responses
from app.schemas.documents import (
    ChunkRead,
    DocumentRead,
    DocumentTitle,
    DocumentUpdate,
    WorkerStatus,
)
from app.services.authorization import STAFF_ROLES, require_role
from app.services.documents import DocumentService

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
    [read] = await service.present([await service.start_ingestion(document)])
    return read


@router.get("", summary="List documents")
async def list_documents(
    member: TenantMembership,
    session: DbSession,
    page: Pagination,
    status_: Annotated[list[DocumentStatus] | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100, description="Title or file name.")] = None,
    visibility: Annotated[
        DocumentVisibility | None, Query(description="Only internal, or only public, documents.")
    ] = None,
    freshness: Annotated[
        Freshness | None,
        Query(
            description="`review_due`: the review date has come. `not_cited`: no answer "
            "cited it recently (see `citations.not_cited`)."
        ),
    ] = None,
) -> Page[DocumentRead]:
    service = DocumentService(session)
    documents, total = await service.list_documents(
        member,
        statuses=status_ or (),
        search=search,
        visibility=visibility,
        freshness=freshness,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=await service.present(documents),
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


@router.get("/{document_id}", summary="Get a document")
async def get_document(
    document_id: uuid.UUID, member: TenantMembership, session: DbSession
) -> DocumentRead:
    service = DocumentService(session)
    [read] = await service.present([await service.get(member, document_id)])
    return read


@router.patch("/{document_id}", summary="Rename, publish or schedule a document's review")
async def update_document(
    document_id: uuid.UUID, body: DocumentUpdate, member: TenantMembership, session: DbSession
) -> DocumentRead:
    """Admins only. `visibility: public` lets the customer assistant answer from the
    document (customers never download it or see its other passages); `internal`
    takes it back at once. `owner_id`, `review_due_on` and `review_interval_days` set
    who keeps it current and when it is next reviewed (send `null` to remove one).
    400 `invalid_owner` unless the owner is active staff here. Every change is recorded
    in the audit log."""
    service = DocumentService(session)
    [read] = await service.present([await service.update(member, document_id, body)])
    return read


@router.post("/{document_id}/reviews", summary="Mark a document reviewed (owner or admin)")
async def review_document(
    document_id: uuid.UUID, member: TenantMembership, user: CurrentUser, session: DbSession
) -> DocumentRead:
    """Confirms the document is current. The next review date becomes today plus its
    `review_interval_days`, or is cleared without one. Recorded in the audit log."""
    service = DocumentService(session)
    [read] = await service.present([await service.mark_reviewed(member, user, document_id)])
    return read


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
    [read] = await service.present([await service.start_ingestion(document)])
    return read


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
    [read] = await service.present([await service.start_ingestion(document)])
    return read


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
