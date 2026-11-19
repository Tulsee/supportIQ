"""Knowledge gaps (Module 13, second part). Staff-only (RLS).

``KnowledgeGap``: questions the assistant couldn't answer, grouped by meaning. A gap
keeps its identity across analyses: new phrasings join it, and an admin's decision
(dismissed, or published as an article) sticks. It carries the latest draft article
(written from resolved tickets, every statement checked against an agent's reply),
the article once published, and the result of asking its questions again afterwards.

``KnowledgeGapQuestion``: one distinct question (by ``query_key``) and the gap it
belongs to, with how often it went unanswered in the look-back window.

``KnowledgeGapJob``: one background job (analyze, draft one gap, or recheck one gap),
for its progress and history. At most one runs per organization at a time.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, str_enum_type


class GapStatus(enum.StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"  # not worth an article: new phrasings still join it, quietly
    PUBLISHED = "published"  # an article was published from it (``document_id``)


class DraftStatus(enum.StrEnum):
    DRAFTED = "drafted"  # an article with at least one verified statement
    NO_SOURCES = "no_sources"  # no resolved ticket looked relevant: nothing to draft from
    NOT_ANSWERED = "not_answered"  # the tickets don't answer it (or nothing passed the checks)
    FAILED = "failed"  # the model couldn't be used (``draft_error``)


class GapJobKind(enum.StrEnum):
    ANALYZE = "analyze"  # collect and group unanswered questions (no model)
    DRAFT = "draft"  # draft an article for one gap
    RECHECK = "recheck"  # ask one published gap's questions again


class GapJobStatus(enum.StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class KnowledgeGap(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_gaps"
    __table_args__ = (
        Index("ix_knowledge_gaps_organization_id_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(Text)  # its most asked question
    status: Mapped[GapStatus] = mapped_column(
        str_enum_type(GapStatus, "knowledge_gap_status"),
        default=GapStatus.OPEN,
        server_default=GapStatus.OPEN.value,
    )
    # Over the look-back window, recomputed by each analysis.
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    asked: Mapped[int] = mapped_column(Integer, default=0)
    last_asked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    # The latest draft.
    draft_status: Mapped[DraftStatus | None] = mapped_column(
        str_enum_type(DraftStatus, "knowledge_gap_draft_status")
    )
    draft_title: Mapped[str | None] = mapped_column(Text)
    draft_body: Mapped[str | None] = mapped_column(Text)  # the statements that passed
    # Every statement with its checks, conflicts with existing documents, the people's
    # details that were looked for, the model's reply.
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The tickets the draft was written from: {id, number, subject, via, similarity}.
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    draft_model: Mapped[str | None] = mapped_column(String(200))
    draft_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    draft_error: Mapped[str | None] = mapped_column(Text)
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The published article.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The questions asked again after publishing: {total, answered, cited_article,
    # questions: [{query, before, after, run_id, cited_article}]}.
    recheck: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rechecked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeGapQuestion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_gap_questions"
    __table_args__ = (UniqueConstraint("organization_id", "query_key"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    gap_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), index=True
    )
    query_key: Mapped[str] = mapped_column(String(500))
    query: Mapped[str] = mapped_column(Text)  # as last asked
    asked: Mapped[int] = mapped_column(Integer, default=0)
    last_asked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    last_status: Mapped[str | None] = mapped_column(String(32))  # the answer status
    # Where it was asked (run kinds) and the tickets it led to (assistant handoffs, the
    # ticket a suggestion was for).
    kinds: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ticket_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KnowledgeGapJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_gap_jobs"
    __table_args__ = (
        Index("ix_knowledge_gap_jobs_organization_id_started_at", "organization_id", "started_at"),
        # One job at a time per organization (drafts and rechecks use the model).
        Index(
            "uq_knowledge_gap_jobs_running",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    kind: Mapped[GapJobKind] = mapped_column(str_enum_type(GapJobKind, "knowledge_gap_job_kind"))
    gap_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[GapJobStatus] = mapped_column(
        str_enum_type(GapJobStatus, "knowledge_gap_job_status"),
        default=GapJobStatus.RUNNING,
        server_default=GapJobStatus.RUNNING.value,
    )
    # Counts: analyze {questions, new_questions, joined, gaps_created, left_for_later};
    # draft {sources, statements, included, conflicts}; recheck {total, answered}.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
