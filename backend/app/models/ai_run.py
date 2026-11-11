import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import text as sql_text  # `text` is also a column name below
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin, str_enum_type
from app.models.document import Document, DocumentChunk
from app.models.user import User


class AIRunKind(enum.StrEnum):
    RETRIEVAL = "retrieval"  # evidence retrieval only (the search playground)
    ANSWER = "answer"  # retrieval, then an answer generated from the selected passages


class AnswerStatus(enum.StrEnum):
    ANSWERED = "answered"  # cites at least one of the passages
    UNCITED = "uncited"  # the model answered but cited no passage: unverified
    DECLINED = "declined"  # the model said the passages don't answer the question
    NO_PASSAGES = "no_passages"  # nothing to answer from; the model wasn't called
    FAILED = "failed"  # the model produced no answer (see ``error``)


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
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    latency_ms: Mapped[float] = mapped_column(Float)
    result_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User | None] = relationship(lazy="raise")
    answer: Mapped["AIAnswer | None"] = relationship(lazy="raise", viewonly=True)


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


class AIAnswer(Base):
    """The generated answer of an ``answer`` run: what the model was given (the
    exact prompt and settings), what it returned, and how its citations checked out."""

    __tablename__ = "ai_answers"

    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    status: Mapped[AnswerStatus] = mapped_column(str_enum_type(AnswerStatus, "answer_status"))
    # The answer shown: citation markers normalized, invented ones removed.
    text: Mapped[str | None] = mapped_column(Text)
    raw_output: Mapped[str | None] = mapped_column(Text)  # exactly what the model returned
    error: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))  # as reported by the provider
    model_version: Mapped[str | None] = mapped_column(String(200))  # e.g. Ollama's digest
    # Sampling, output and context limits: everything else that shapes the output.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    prompt_version: Mapped[str] = mapped_column(String(40))
    system_prompt: Mapped[str] = mapped_column(Text)
    user_prompt: Mapped[str] = mapped_column(Text)
    passage_count: Mapped[int] = mapped_column(Integer)  # passages in the prompt
    omitted_passages: Mapped[int] = mapped_column(Integer)  # left out to fit the context
    # Passage numbers the model cited that it was never given (removed from ``text``).
    invalid_citations: Mapped[list[int]] = mapped_column(
        JSONB, server_default=sql_text("'[]'::jsonb")
    )
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    latency_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    citations: Mapped[list["Citation"]] = relationship(
        lazy="raise", order_by="Citation.answer_start, Citation.passage_number"
    )


class Citation(UUIDPrimaryKeyMixin, Base):
    """A passage an answer cites. It is always one of the run's selected passages:
    the model can only cite by number, and numbers are resolved here. Where the
    passage came from is copied, so the reference survives a re-index."""

    __tablename__ = "citations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_answers.ai_run_id", ondelete="CASCADE"), index=True
    )
    retrieval_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("retrieval_results.id", ondelete="CASCADE")
    )
    passage_number: Mapped[int] = mapped_column(Integer)  # the [n] the model wrote
    # The claim the citation supports: a character range of the answer text.
    answer_start: Mapped[int] = mapped_column(Integer)
    answer_end: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    document_version: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    section_heading: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document | None] = relationship(lazy="raise")
