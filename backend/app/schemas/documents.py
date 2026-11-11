import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints

from ai_engine.ingestion.extract import DocumentType
from app.models import Document, DocumentChunk, DocumentStatus
from app.schemas.tickets import PersonSummary

DocumentTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


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
    version: int = Field(description="Uploads of this document so far.")
    indexed_version: int | None = Field(
        description="The version currently searchable; may trail `version` while a new "
        "version is processed or if it failed."
    )
    page_count: int | None
    chunk_count: int
    token_count: int
    embedding_model: str | None
    uploaded_by: PersonSummary | None
    created_at: datetime
    updated_at: datetime
    processing_started_at: datetime | None
    indexed_at: datetime | None

    @classmethod
    def build(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            title=document.title,
            filename=document.filename,
            file_type=document.file_type,
            size_bytes=document.size_bytes,
            status=document.status,
            error=document.error,
            version=document.version,
            indexed_version=document.indexed_version,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            token_count=document.token_count,
            embedding_model=document.embedding_model,
            uploaded_by=PersonSummary.build(document.uploaded_by),
            created_at=document.created_at,
            updated_at=document.updated_at,
            processing_started_at=document.processing_started_at,
            indexed_at=document.indexed_at,
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


class DocumentSummary(BaseModel):
    id: uuid.UUID
    title: str
    file_type: DocumentType


class SearchHit(BaseModel):
    score: float = Field(description="Cosine similarity between the query and the chunk.")
    document: DocumentSummary
    chunk: ChunkRead


class SearchResults(BaseModel):
    query: str
    embedding_model: str
    results: list[SearchHit]
