import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin, str_enum_type
from app.models.document import Document, DocumentChunk
from app.models.user import User


class AIRunKind(enum.StrEnum):
    RETRIEVAL = "retrieval"  # evidence retrieval (later: answer generation runs)


class AIRun(UUIDPrimaryKeyMixin, Base):
    """One execution of the AI engine, recorded for inspection and evaluation:
    the query, how it was analyzed, the exact configuration, and stage timings."""

    __tablename__ = "ai_runs"
    __table_args__ = (
        Index("ix_ai_runs_organization_id_created_at", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[AIRunKind] = mapped_column(str_enum_type(AIRunKind, "ai_run_kind"))
    query: Mapped[str] = mapped_column(Text)
    query_type: Mapped[str] = mapped_column(String(40))
    requested_strategy: Mapped[str] = mapped_column(String(20))
    strategy: Mapped[str] = mapped_column(String(20))
    # Everything needed to reproduce the run: models, top-k values, fusion weights,
    # BM25 parameters, and the analysis' reasons.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    latency_ms: Mapped[float] = mapped_column(Float)
    result_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User | None] = relationship(lazy="raise")


class RetrievalResult(UUIDPrimaryKeyMixin, Base):
    """A candidate considered by a retrieval run, with its score at every stage.
    ``final_rank`` is set for the passages the run selected."""

    __tablename__ = "retrieval_results"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), index=True
    )
    # The chunk may be replaced by a re-index later; its provenance stays.
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    document_version: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    sparse_rank: Mapped[int | None] = mapped_column(Integer)
    sparse_score: Mapped[float | None] = mapped_column(Float)
    dense_rank: Mapped[int | None] = mapped_column(Integer)
    dense_score: Mapped[float | None] = mapped_column(Float)
    fused_rank: Mapped[int] = mapped_column(Integer)
    fused_score: Mapped[float] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)
    final_rank: Mapped[int | None] = mapped_column(Integer)

    chunk: Mapped[DocumentChunk | None] = relationship(lazy="raise")
    document: Mapped[Document | None] = relationship(lazy="raise")
