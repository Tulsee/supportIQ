"""Knowledge-base contradictions (Module 13). Staff-only (RLS).

``ContradictionCheck``: one judged pair of passages from different documents. Every
judged pair is kept (identified by the two texts, ``pair_key``), so a pair is sent to
the model once and again only when one of its passages changes. A pair the model
reports as conflicting, with quotes found in both passages, is ``flagged``: a finding
that admins review (``open``, then ``dismissed`` or ``resolved``). The passages are
copied, so a finding still reads correctly after its document is re-indexed; it is
then resolved automatically (``document_changed``) and the new text is checked anew.

``ContradictionScan``: one run of the scanner, for its progress and history.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
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


class FindingStatus(enum.StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"  # not a real conflict (or acceptable): not reported again
    RESOLVED = "resolved"  # fixed; automatically when a passage's document changed


class ScanStatus(enum.StrEnum):
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


class ContradictionCheck(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "contradiction_checks"
    __table_args__ = (
        UniqueConstraint("organization_id", "pair_key"),
        Index("ix_contradiction_checks_organization_id_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    pair_key: Mapped[str] = mapped_column(String(64))
    document_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    document_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Cleared when a re-index replaces the chunk (the passage copies remain).
    chunk_a_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    chunk_b_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    section_a: Mapped[str | None] = mapped_column(Text)
    section_b: Mapped[str | None] = mapped_column(Text)
    passage_a: Mapped[str] = mapped_column(Text)
    passage_b: Mapped[str] = mapped_column(Text)
    similarity: Mapped[float] = mapped_column(Float)
    # "conflict", "consistent" or "unrelated"; None when the model couldn't be used
    # (``error``), and the pair is tried again by the next scan.
    verdict: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    flagged: Mapped[bool] = mapped_column(default=False)
    # The verified conflicts: topic, quotes, explanation, whether each quote was found.
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Every model judgement (one per order asked), and the policy that decided.
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    model: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    # Findings only (flagged pairs).
    status: Mapped[FindingStatus | None] = mapped_column(
        str_enum_type(FindingStatus, "contradiction_status")
    )
    status_reason: Mapped[str | None] = mapped_column(String(32))
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ContradictionScan(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "contradiction_scans"
    __table_args__ = (
        Index("ix_contradiction_scans_organization_id_started_at", "organization_id", "started_at"),
        # At most one scan runs per organization: a second one can't even be recorded.
        Index(
            "uq_contradiction_scans_running",
            "organization_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    trigger: Mapped[str] = mapped_column(String(16))  # "manual" or "scheduled"
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ScanStatus] = mapped_column(
        str_enum_type(ScanStatus, "contradiction_scan_status"),
        default=ScanStatus.RUNNING,
        server_default=ScanStatus.RUNNING.value,
    )
    # Candidate pairs found, pairs sent to the model in this run (new or changed, up
    # to the per-run limit), pairs left for the next run, new findings, model failures.
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    checked: Mapped[int] = mapped_column(Integer, default=0)
    remaining: Mapped[int] = mapped_column(Integer, default=0)
    findings: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    auto_resolved: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
