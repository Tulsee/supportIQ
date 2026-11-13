import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_engine.ingestion.extract import DocumentType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum_type
from app.models.user import User


class DocumentStatus(enum.StrEnum):
    QUEUED = "queued"  # waiting for a worker
    PROCESSING = "processing"
    INDEXED = "indexed"  # searchable
    FAILED = "failed"  # see Document.error; can be retried


ACTIVE_INGESTION = (DocumentStatus.QUEUED, DocumentStatus.PROCESSING)


class DocumentVisibility(enum.StrEnum):
    INTERNAL = "internal"  # staff only (the default)
    PUBLIC = "public"  # also answers customers' questions (Module 12); enforced by RLS


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A knowledge-base document. The original file lives in object storage.

    ``version`` counts uploads of this document; ``indexed_version`` is the one
    currently searchable. A failed re-index keeps the previous chunks searchable.
    Each ingestion attempt gets a fresh ``ingestion_id``; a job for any other id is
    obsolete (superseded by a retry or a new version) and does nothing.
    """

    __tablename__ = "documents"
    __table_args__ = (
        # The same file can't be uploaded twice into one organization.
        UniqueConstraint("organization_id", "sha256"),
        Index("ix_documents_organization_id_updated_at", "organization_id", "updated_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(255))  # sanitized original name
    file_type: Mapped[DocumentType] = mapped_column(str_enum_type(DocumentType, "document_type"))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, default=1)
    indexed_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum_type(DocumentStatus, "document_status"),
        default=DocumentStatus.QUEUED,
        server_default=DocumentStatus.QUEUED.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[DocumentVisibility] = mapped_column(
        str_enum_type(DocumentVisibility, "document_visibility"),
        default=DocumentVisibility.INTERNAL,
        server_default=DocumentVisibility.INTERNAL.value,
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    uploaded_by: Mapped[User | None] = relationship(lazy="raise")


class DocumentChunk(UUIDPrimaryKeyMixin, Base):
    """A searchable piece of a document. Its id is also its Qdrant point id."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    document_version: Mapped[int] = mapped_column(Integer)
    ingestion_id: Mapped[uuid.UUID]
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)  # first page covered
    page_end: Mapped[int | None] = mapped_column(Integer)  # last page covered
    section_heading: Mapped[str | None] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)  # into the document's extracted text
    end_offset: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding_model: Mapped[str] = mapped_column(String(200))
    # Keyword search (BM25-style ranking builds on this in the retrieval module).
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english'::regconfig, content)", persisted=True),
        deferred=True,
        deferred_raiseload=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(lazy="raise")
