"""Knowledge gaps to articles (Module 13)."""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field

from app.models import (
    DocumentStatus,
    DocumentVisibility,
    DraftStatus,
    GapJobKind,
    GapJobStatus,
    GapStatus,
    KnowledgeGapJob,
    KnowledgeGapQuestion,
)
from app.schemas.tickets import PersonSummary
from app.services.knowledge_gaps import GapRow, GapSummary


class GapQuestionRead(BaseModel):
    query: str = Field(description="As last asked.")
    asked: int = Field(description="Times not answered in the look-back window.")
    last_asked: datetime | None
    last_status: str | None = Field(
        description="`abstained`, `declined` or `no_passages` when last asked."
    )
    last_run_id: uuid.UUID | None = Field(description="The latest run, to inspect.")
    kinds: list[str] = Field(
        description="Where it was asked: `answer`, `suggestion`, `self_service`."
    )
    ticket_ids: list[uuid.UUID] = Field(
        description="Tickets it led to (assistant handoffs, the ticket a suggestion was for)."
    )

    @classmethod
    def build(cls, q: KnowledgeGapQuestion) -> Self:
        return cls(
            query=q.query,
            asked=q.asked,
            last_asked=q.last_asked,
            last_status=q.last_status,
            last_run_id=q.last_run_id,
            kinds=list(q.kinds or []),
            ticket_ids=[uuid.UUID(t) for t in q.ticket_ids or []],
        )


class GapSource(BaseModel):
    id: uuid.UUID
    number: int
    subject: str
    via: Literal["linked", "similar"] = Field(
        description="`linked`: the questions led to this ticket; `similar`: found by search."
    )
    similarity: float | None = Field(description="To the gap's questions (similar tickets).")


class DraftStatement(BaseModel):
    text: str
    ticket: int = Field(description="The number of the ticket it comes from.")
    quote: str = Field(description="The agent's sentence it rests on.")
    included: bool = Field(description="Passed every check: part of the body.")
    problems: list[Literal["quote_not_found", "number_not_in_quote", "private", "conflict"]]
    private: list[str] = Field(description="Customer details found in the text.")


class DraftConflict(BaseModel):
    document_id: uuid.UUID
    document_title: str
    section: str | None
    topic: str
    quote_draft: str
    quote_document: str
    explanation: str


class GapDraft(BaseModel):
    status: DraftStatus
    verdict: Literal["answered", "partial", "not_answered"] | None
    title: str | None
    body: str | None = Field(description="The statements that passed every check.")
    statements: list[DraftStatement]
    conflicts: list[DraftConflict] = Field(
        description="Where the draft contradicts the knowledge base (the draft's side is "
        "left out of the body)."
    )
    passages_checked: int = Field(description="Knowledge-base passages it was checked against.")
    model: str | None
    latency_ms: int
    error: str | None
    drafted_at: datetime | None


class GapArticle(BaseModel):
    document_id: uuid.UUID | None = Field(description="None if the document was deleted.")
    title: str | None
    status: DocumentStatus | None
    visibility: DocumentVisibility | None
    published_by: PersonSummary | None
    published_at: datetime | None


class RecheckQuestion(BaseModel):
    query: str
    before: str | None
    after: str | None
    run_id: uuid.UUID
    cited_article: bool


class GapRecheck(BaseModel):
    total: int
    answered: int
    cited_article: int = Field(description="Answers that cite the published article.")
    questions: list[RecheckQuestion]
    checked_at: datetime | None


class GapRead(BaseModel):
    id: uuid.UUID
    label: str = Field(description="Its most asked question.")
    status: GapStatus
    question_count: int = Field(description="Distinct questions asked in the window.")
    asked: int = Field(description="Times its questions went unanswered in the window.")
    last_asked: datetime | None
    asked_since_published: int = Field(
        description="Questions still unanswered after the article was published."
    )
    questions: list[GapQuestionRead] = Field(
        description="All of them in the detail view; the three most asked in lists."
    )
    sources: list[GapSource]
    draft: GapDraft | None
    article: GapArticle | None
    recheck: GapRecheck | None
    note: str | None
    reviewed_by: PersonSummary | None
    reviewed_at: datetime | None
    created_at: datetime

    @classmethod
    def build(cls, row: GapRow) -> Self:
        g = row.gap
        data: dict[str, Any] = g.draft or {}
        draft = None
        if g.draft_status is not None:
            draft = GapDraft(
                status=g.draft_status,
                verdict=data.get("verdict"),
                title=g.draft_title,
                body=g.draft_body,
                statements=[
                    DraftStatement(
                        text=s.get("text", ""),
                        ticket=int(s.get("ticket") or 0),
                        quote=s.get("quote", ""),
                        included=bool(s.get("included")),
                        problems=s.get("problems", []),
                        private=s.get("private", []),
                    )
                    for s in data.get("statements", [])
                ],
                conflicts=[DraftConflict(**c) for c in data.get("conflicts", [])],
                passages_checked=len(data.get("conflict_checks", [])),
                model=g.draft_model,
                latency_ms=g.draft_latency_ms,
                error=g.draft_error,
                drafted_at=g.drafted_at,
            )
        article = None
        if g.status == GapStatus.PUBLISHED:
            d = row.document
            article = GapArticle(
                document_id=d.id if d else None,
                title=d.title if d else None,
                status=d.status if d else None,
                visibility=d.visibility if d else None,
                published_by=PersonSummary.build(row.publisher),
                published_at=g.published_at,
            )
        recheck = None
        if g.recheck:
            recheck = GapRecheck(**g.recheck, checked_at=g.rechecked_at)
        return cls(
            id=g.id,
            label=g.label,
            status=g.status,
            question_count=g.question_count,
            asked=g.asked,
            last_asked=g.last_asked,
            asked_since_published=row.asked_since_published,
            questions=[GapQuestionRead.build(q) for q in row.questions or []],
            sources=[GapSource(**s) for s in g.sources or []],
            draft=draft,
            article=article,
            recheck=recheck,
            note=g.note,
            reviewed_by=PersonSummary.build(row.reviewer),
            reviewed_at=g.reviewed_at,
            created_at=g.created_at,
        )


class GapReview(BaseModel):
    status: Literal[GapStatus.OPEN, GapStatus.DISMISSED] = Field(
        description="`dismissed` (not worth an article; new phrasings still join it quietly) "
        "or `open` (reopen)."
    )
    note: str | None = Field(default=None, max_length=1000)


class GapPublish(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000, description="Markdown.")
    visibility: DocumentVisibility = Field(
        default=DocumentVisibility.INTERNAL,
        description="`public` lets the customer assistant answer from it; `internal` keeps it "
        "to staff.",
    )


class GapJobRead(BaseModel):
    id: uuid.UUID
    kind: GapJobKind
    gap_id: uuid.UUID | None
    status: GapJobStatus
    result: dict[str, Any] = Field(
        description="analyze: questions, new_questions, joined, gaps_created, left_for_later; "
        "draft: status, sources, statements, included, conflicts; recheck: total, answered."
    )
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def build(cls, job: KnowledgeGapJob) -> Self:
        return cls(
            id=job.id,
            kind=job.kind,
            gap_id=job.gap_id,
            status=job.status,
            result=job.result or {},
            error=job.error,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class GapSummaryRead(BaseModel):
    open: int
    dismissed: int
    published: int
    drafted: int = Field(description="Open gaps with a draft ready to review.")
    questions: int = Field(description="Distinct unanswered questions in the window.")
    lookback_days: int
    latest_job: GapJobRead | None
    latest_analysis: GapJobRead | None

    @classmethod
    def build(cls, s: GapSummary, lookback_days: int) -> Self:
        return cls(
            open=s.open,
            dismissed=s.dismissed,
            published=s.published,
            drafted=s.drafted,
            questions=s.questions,
            lookback_days=lookback_days,
            latest_job=GapJobRead.build(s.latest_job) if s.latest_job else None,
            latest_analysis=GapJobRead.build(s.latest_analysis) if s.latest_analysis else None,
        )
