"""AI ticket triage (Module 11)."""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field

from app.models import TicketTriage, TriageDecisionKind, TriageOutcome, User
from app.schemas.tickets import PersonSummary


class TriageCategory(BaseModel):
    id: uuid.UUID | None = Field(description="Null if the category was deleted since.")
    key: str
    name: str


class LlmSignal(BaseModel):
    category: str | None = Field(description="Its pick (a key), or null when unsure.")
    confidence: float = Field(description="Its self-reported confidence as a score.")
    priority: str | None
    language: str | None
    urgency_cues: list[str]


class NeighbourExample(BaseModel):
    ticket_id: uuid.UUID
    number: int | None
    category: str
    similarity: float


class NeighbourSignal(BaseModel):
    shares: dict[str, float] = Field(description="Each category's share of the weighted vote.")
    examples: list[NeighbourExample] = Field(description="The similar labelled tickets.")


class TriageReview(BaseModel):
    outcome: TriageOutcome
    final_category_key: str | None
    reviewed_at: datetime
    reviewed_by: PersonSummary | None


class TriageRead(BaseModel):
    id: uuid.UUID
    mode: Literal["shadow", "on"] = Field(description="The triage mode when it ran.")
    decision: TriageDecisionKind = Field(
        description="`confident` (at or above the threshold), `unsure`, or `failed` (the "
        "model couldn't be used)."
    )
    applied: bool = Field(description="The category was set on the ticket.")
    category: TriageCategory | None = Field(description="The top guess, even when unsure.")
    confidence: float = Field(description="The combined score of the top guess (0-1).")
    threshold: float
    agree: bool | None = Field(description="Both signals picked the same category.")
    priority: str | None = Field(description="Suggested priority (never applied).")
    language: str | None
    urgency_cues: list[str]
    llm: LlmSignal | None
    neighbours: NeighbourSignal | None
    model: str | None
    error: str | None
    latency_ms: int
    created_at: datetime
    review: TriageReview | None = Field(description="What a person did with it, once they did.")

    @classmethod
    def build(cls, row: TicketTriage, reviewer: User | None) -> Self:
        signals = row.signals or {}
        return cls(
            id=row.id,
            mode=row.mode,  # type: ignore[arg-type]
            decision=row.decision,
            applied=row.applied,
            category=(
                TriageCategory(
                    id=row.category_id,
                    key=row.category_key,
                    name=row.category_name or row.category_key,
                )
                if row.category_key
                else None
            ),
            confidence=row.confidence,
            threshold=row.threshold,
            agree=row.agree,
            priority=row.priority,
            language=row.language,
            urgency_cues=row.urgency_cues or [],
            llm=LlmSignal.model_validate(signals["llm"]) if signals.get("llm") else None,
            neighbours=(
                NeighbourSignal.model_validate(signals["neighbours"])
                if signals.get("neighbours")
                else None
            ),
            model=row.model,
            error=row.error,
            latency_ms=row.latency_ms,
            created_at=row.created_at,
            review=(
                TriageReview(
                    outcome=row.outcome,
                    final_category_key=row.final_category_key,
                    reviewed_at=row.reviewed_at,
                    reviewed_by=PersonSummary.build(reviewer),
                )
                if row.outcome and row.reviewed_at
                else None
            ),
        )
