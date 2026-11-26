import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints

from ai_engine.ingestion.extract import DocumentType
from app.models import Document, DocumentChunk, DocumentStatus, DocumentVisibility
from app.repositories.documents import CitationUsage, UsageWindow
from app.schemas.tickets import PersonSummary

DocumentTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


class QuarantinedSentence(BaseModel):
    text: str
    rule: Literal["addresses_ai", "override"] = Field(
        description="`addresses_ai`: it tells an AI what to do; `override`: it uses the "
        'language of prompt overrides ("ignore all previous instructions").'
    )
    page: int | None
    heading: str | None


class DocumentCitations(BaseModel):
    """How much answers people were given used the document (evaluation probes and gap
    rechecks don't count)."""

    last_cited_at: datetime | None = Field(description="The latest answer that cited it.")
    recent: int = Field(description="Answers that cited it in the last `window_days`.")
    window_days: int
    not_cited: bool = Field(
        description="Indexed long enough to judge, and no answer cited it in the window: "
        "a candidate for review or removal."
    )


class DocumentRead(BaseModel):
    id: uuid.UUID
    title: str
    filename: str
    file_type: DocumentType
    size_bytes: int
    status: DocumentStatus
    error: str | None = Field(
        description="Why indexing failed, or warnings (e.g. unreadable pages) after indexing."
    )
    visibility: DocumentVisibility = Field(
        description="`internal`: staff only. `public`: also answers customers' questions "
        "in the portal."
    )
    version: int = Field(description="Uploads of this document so far.")
    indexed_version: int | None = Field(
        description="The version currently searchable; may trail `version` while a new "
        "version is processed or if it failed."
    )
    page_count: int | None
    quarantined: list[QuarantinedSentence] = Field(
        description="Sentences left out when indexing because they are instructions "
        "aimed at an AI: never searched, never shown to the model or as a source."
    )
    chunk_count: int
    token_count: int
    embedding_model: str | None
    uploaded_by: PersonSummary | None
    owner: PersonSummary | None = Field(description="The staff member who keeps it current.")
    review_due_on: date | None = Field(description="When it should next be reviewed.")
    review_interval_days: int | None = Field(
        description="After a review, the next review date is this many days later."
    )
    review_due: bool = Field(description="The review date has come (UTC).")
    reviewed_at: datetime | None
    reviewed_by: PersonSummary | None
    citations: DocumentCitations
    created_at: datetime
    updated_at: datetime
    processing_started_at: datetime | None
    indexed_at: datetime | None

    @classmethod
    def build(
        cls,
        document: Document,
        *,
        usage: CitationUsage | None,
        window: UsageWindow,
        window_days: int,
        today: date,
    ) -> Self:
        last_cited = usage.last_cited_at if usage else None
        not_cited = (
            document.indexed_at is not None
            and document.indexed_at <= window.judged_before
            and (last_cited is None or last_cited < window.since)
        )
        return cls(
            id=document.id,
            title=document.title,
            filename=document.filename,
            file_type=document.file_type,
            size_bytes=document.size_bytes,
            status=document.status,
            error=document.error,
            visibility=document.visibility,
            version=document.version,
            indexed_version=document.indexed_version,
            page_count=document.page_count,
            quarantined=[QuarantinedSentence(**q) for q in document.quarantined or []],
            chunk_count=document.chunk_count,
            token_count=document.token_count,
            embedding_model=document.embedding_model,
            uploaded_by=PersonSummary.build(document.uploaded_by),
            owner=PersonSummary.build(document.owner),
            review_due_on=document.review_due_on,
            review_interval_days=document.review_interval_days,
            review_due=document.review_due_on is not None and document.review_due_on <= today,
            reviewed_at=document.reviewed_at,
            reviewed_by=PersonSummary.build(document.reviewed_by),
            citations=DocumentCitations(
                last_cited_at=last_cited,
                recent=usage.recent if usage else 0,
                window_days=window_days,
                not_cited=not_cited,
            ),
            created_at=document.created_at,
            updated_at=document.updated_at,
            processing_started_at=document.processing_started_at,
            indexed_at=document.indexed_at,
        )


class DocumentUpdate(BaseModel):
    """Admin only. Only the fields provided are changed."""

    title: DocumentTitle | None = None
    visibility: DocumentVisibility | None = Field(
        default=None,
        description="`public` lets the customer assistant answer from this document; "
        "`internal` keeps it to staff.",
    )
    owner_id: uuid.UUID | None = Field(
        default=None,
        description="A staff member who keeps the document current and is reminded when "
        "its review is due; `null` removes the owner (admins are reminded instead).",
    )
    review_due_on: date | None = Field(
        default=None, description="When the document should next be reviewed; `null` removes it."
    )
    review_interval_days: Annotated[int, Field(ge=1, le=730)] | None = Field(
        default=None,
        description="After a review, set the next review date this many days later; `null`: "
        "a review clears the date.",
    )


class ChunkRead(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_version: int
    chunk_index: int
    page_number: int | None = Field(description="First page the chunk covers (PDF).")
    page_end: int | None = Field(description="Last page the chunk covers (PDF).")
    section_heading: str | None
    start_offset: int = Field(description="Character offset in the extracted document text.")
    end_offset: int
    token_count: int
    content: str
    embedding_model: str

    @classmethod
    def build(cls, chunk: DocumentChunk) -> Self:
        return cls(
            id=chunk.id,
            document_id=chunk.document_id,
            document_version=chunk.document_version,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            page_end=chunk.page_end,
            section_heading=chunk.section_heading,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            token_count=chunk.token_count,
            content=chunk.content,
            embedding_model=chunk.embedding_model,
        )


class WorkerStatus(BaseModel):
    status: Literal["online", "offline", "unknown"] = Field(
        description="Whether a background worker is running to index queued documents."
    )
    workers: int | None


class DocumentSummary(BaseModel):
    id: uuid.UUID
    title: str
    file_type: DocumentType
