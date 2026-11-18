"""Knowledge-base contradictions (Module 13)."""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

from app.models import ContradictionScan, FindingStatus, ScanStatus
from app.schemas.tickets import PersonSummary
from app.services.contradictions import FindingRow, ScanSummary


class ConflictRead(BaseModel):
    topic: str
    quote_a: str = Field(description="The conflicting sentence in passage A.")
    quote_b: str = Field(description="The conflicting sentence in passage B.")
    explanation: str
    grounded: bool = Field(description="Both quotes were found in their passages.")


class FindingPassage(BaseModel):
    document_id: uuid.UUID
    document_title: str
    section: str | None
    content: str = Field(description="The passage as it was when checked.")
    current: bool = Field(
        description="The passage is still in the knowledge base (false after its document "
        "was re-indexed or a new version uploaded)."
    )


class FindingRead(BaseModel):
    id: uuid.UUID
    status: FindingStatus
    status_reason: str | None = Field(
        description="`document_changed` when resolved automatically because a passage was replaced."
    )
    a: FindingPassage
    b: FindingPassage
    conflicts: list[ConflictRead] = Field(
        description="What conflicts, quoted from both passages (grounded ones first)."
    )
    confidence: float = Field(description="The model's self-reported confidence (0-1).")
    similarity: float = Field(description="How similar the two passages are (cosine).")
    model: str | None
    note: str | None
    reviewed_by: PersonSummary | None
    reviewed_at: datetime | None
    created_at: datetime

    @classmethod
    def build(cls, row: FindingRow) -> Self:
        c = row.check
        return cls(
            id=c.id,
            status=c.status or FindingStatus.OPEN,
            status_reason=c.status_reason,
            a=FindingPassage(
                document_id=c.document_a_id,
                document_title=row.document_a_title,
                section=c.section_a,
                content=c.passage_a,
                current=c.chunk_a_id is not None,
            ),
            b=FindingPassage(
                document_id=c.document_b_id,
                document_title=row.document_b_title,
                section=c.section_b,
                content=c.passage_b,
                current=c.chunk_b_id is not None,
            ),
            conflicts=sorted(
                (
                    ConflictRead(
                        topic=item.get("topic") or "",
                        quote_a=item.get("quote_a") or "",
                        quote_b=item.get("quote_b") or "",
                        explanation=item.get("explanation") or "",
                        grounded=bool(item.get("found_a") and item.get("found_b")),
                    )
                    for item in c.conflicts
                ),
                key=lambda conflict: not conflict.grounded,
            ),
            confidence=c.confidence,
            similarity=c.similarity,
            model=c.model,
            note=c.note,
            reviewed_by=PersonSummary.build(row.reviewer),
            reviewed_at=c.reviewed_at,
            created_at=c.created_at,
        )


class FindingReview(BaseModel):
    status: FindingStatus = Field(
        description="`dismissed` (not a real conflict; not reported again for this text), "
        "`resolved` (fixed), or `open` (reopen)."
    )
    note: str | None = Field(default=None, max_length=1000)


class ScanRead(BaseModel):
    id: uuid.UUID
    trigger: Literal["manual", "scheduled"]
    status: ScanStatus
    candidates: int = Field(description="Pairs of similar passages from different documents.")
    checked: int = Field(description="Pairs the model judged in this scan (new or changed).")
    remaining: int = Field(description="Pairs left for the next scan (per-scan limit).")
    findings: int = Field(description="New findings.")
    failed: int = Field(description="Pairs the model couldn't judge (tried again next scan).")
    auto_resolved: int = Field(description="Findings resolved because a passage changed.")
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def build(cls, scan: ContradictionScan) -> Self:
        return cls(
            id=scan.id,
            trigger="manual" if scan.trigger == "manual" else "scheduled",
            status=scan.status,
            candidates=scan.candidates,
            checked=scan.checked,
            remaining=scan.remaining,
            findings=scan.findings,
            failed=scan.failed,
            auto_resolved=scan.auto_resolved,
            error=scan.error,
            started_at=scan.started_at,
            finished_at=scan.finished_at,
        )


class ContradictionSummary(BaseModel):
    open: int
    dismissed: int
    resolved: int
    pairs_checked: int = Field(description="Pairs of passages judged so far.")
    scanning_enabled: bool = Field(description="Scheduled scans are on (organization setting).")
    latest_scan: ScanRead | None

    @classmethod
    def build(cls, summary: ScanSummary) -> Self:
        return cls(
            open=summary.open,
            dismissed=summary.dismissed,
            resolved=summary.resolved,
            pairs_checked=summary.pairs_checked,
            scanning_enabled=summary.scanning_enabled,
            latest_scan=ScanRead.build(summary.latest) if summary.latest else None,
        )
