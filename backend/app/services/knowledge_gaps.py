"""Knowledge gaps to articles (Module 13, second part).

1. **Analyze** (no model): the questions the assistant didn't answer (abstained, the
   model declined, or nothing to answer from) in the look-back window, from staff
   searches, ticket suggestions and the customer assistant, are grouped by meaning
   into gaps (``ai_engine.gaps.cluster``). New phrasings join existing gaps, so an
   admin's decision about a gap sticks. Escalations (documents disagree) and
   clarifying questions are left out: the contradiction scanner and the question
   itself are the fixes for those.
2. **Draft** (one gap, model): resolved tickets with a public agent reply are the
   sources: first those the questions led to (a customer handed off from the
   assistant, the ticket a suggestion was for), then similar ones (a keyword search,
   kept when the embedding similarity reaches a threshold). The model writes an
   article whose every statement quotes an agent's reply; quotes, numbers and the
   customers' details are checked (``ai_engine.gaps.draft``), and the draft is checked
   against the nearest knowledge-base passages with the contradiction judge. Only the
   statements that pass go into the body.
3. **Publish** (admin): the edited article becomes a Markdown document (internal or
   public) and is indexed like any upload.
4. **Recheck** (one published gap, model): its questions are asked again through the
   staff answer pipeline, to see how many are now answered and from the article.
   Those runs are marked (``config.origin``), like the evaluation's probes: runs with an
   origin are never collected as gaps.

Jobs run in a worker, one at a time per organization (a partial unique index).
"""

import logging
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from anyio import to_thread
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.contradictions import judge
from ai_engine.contradictions.judge import PassageText, ScanPolicy
from ai_engine.embeddings.embedder import Embedder
from ai_engine.evidence.nli import NliModel
from ai_engine.evidence.signals import terms
from ai_engine.gaps import cluster as clustering
from ai_engine.gaps import draft as drafting
from ai_engine.gaps.draft import Draft, TicketSource
from ai_engine.generation.llm import LLMClient, LLMError, Prompt
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.database import set_rls_context
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.core.storage import ObjectStorage
from app.models import (
    AIAnswer,
    AIRun,
    AIRunKind,
    AnswerStatus,
    AssistantConversation,
    AssistantMessage,
    Customer,
    Document,
    DocumentChunk,
    DocumentVisibility,
    DraftStatus,
    GapJobKind,
    GapJobStatus,
    GapStatus,
    KnowledgeGap,
    KnowledgeGapJob,
    KnowledgeGapQuestion,
    MessageAuthorType,
    OrganizationMember,
    OrgRole,
    Ticket,
    TicketMessage,
    TicketStatus,
    User,
)
from app.schemas.documents import DocumentUpdate
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.workers.queue import GapQueue, IngestionQueue, QueueUnavailableError

logger = logging.getLogger(__name__)

MAX_PAGE = 100
# A job still "running" after this long was interrupted (a worker crashed).
STALE_JOB = timedelta(hours=2)
# Marks the runs of a recheck. Runs with any origin (rechecks, evaluation probes) are
# not people's questions and are never collected as gaps.
GAP_CHECK_ORIGIN = "gap_check"
# Answer statuses that mean the knowledge base didn't have the answer.
GAP_STATUSES = (AnswerStatus.ABSTAINED, AnswerStatus.DECLINED, AnswerStatus.NO_PASSAGES)
GAP_KINDS = (AIRunKind.ANSWER, AIRunKind.SUGGESTION, AIRunKind.SELF_SERVICE)
SOURCE_STATUSES = (TicketStatus.RESOLVED, TicketStatus.CLOSED)
# Questions per gap embedded to place new questions, and shown to the model.
CENTROID_QUESTIONS = 20
PROMPT_QUESTIONS = 6


def _now() -> datetime:
    return datetime.now(UTC)


# --- 1. Analyze ---------------------------------------------------------------------------


@dataclass
class AskedQuestion:
    """One distinct unanswered question over the window."""

    key: str
    query: str  # as last asked
    asked: int = 0
    last_asked: datetime | None = None
    last_run_id: uuid.UUID | None = None
    last_status: str | None = None
    kinds: set[str] = field(default_factory=set)
    ticket_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class AnalysisResult:
    questions: int
    new_questions: int
    joined: int
    gaps_created: int
    left_for_later: int

    def to_json(self) -> dict[str, int]:
        return {
            "questions": self.questions,
            "new_questions": self.new_questions,
            "joined": self.joined,
            "gaps_created": self.gaps_created,
            "left_for_later": self.left_for_later,
        }


class GapAnalyzer:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        lookback_days: int | None = None,
        join_similarity: float | None = None,
        cluster_similarity: float | None = None,
        max_new_questions: int | None = None,
    ) -> None:
        self.session = session
        self.embedder = embedder
        self.lookback = timedelta(days=lookback_days or settings.gap_lookback_days)
        self.join_similarity = (
            settings.gap_join_similarity if join_similarity is None else join_similarity
        )
        self.cluster_similarity = (
            settings.gap_cluster_similarity if cluster_similarity is None else cluster_similarity
        )
        self.max_new = max_new_questions or settings.gap_max_new_questions

    async def collect(self, organization_id: uuid.UUID) -> list[AskedQuestion]:
        """Unanswered questions in the window, one per ``query_key``, oldest run first
        so the latest wording and status win."""
        rows = (
            await self.session.execute(
                select(
                    AIRun.id,
                    AIRun.query,
                    AIRun.kind,
                    AIRun.ticket_id,
                    AIRun.created_at,
                    AIAnswer.status,
                    AssistantConversation.ticket_id,
                )
                .join(AIAnswer, AIAnswer.ai_run_id == AIRun.id)
                .outerjoin(AssistantMessage, AssistantMessage.ai_run_id == AIRun.id)
                .outerjoin(
                    AssistantConversation,
                    AssistantConversation.id == AssistantMessage.conversation_id,
                )
                .where(
                    AIRun.organization_id == organization_id,
                    AIRun.created_at >= _now() - self.lookback,
                    AIRun.kind.in_(GAP_KINDS),
                    AIAnswer.status.in_(GAP_STATUSES),
                    AIRun.config["origin"].astext.is_(None),
                )
                .order_by(AIRun.created_at, AIRun.id)
            )
        ).all()
        questions: dict[str, AskedQuestion] = {}
        for run_id, query, kind, ticket_id, created_at, status, handoff_ticket_id in rows:
            key = clustering.query_key(query)
            if not key:
                continue
            q = questions.setdefault(key, AskedQuestion(key=key, query=query.strip()))
            q.query = query.strip()
            q.asked += 1
            q.last_asked = created_at
            q.last_run_id = run_id
            q.last_status = status.value
            q.kinds.add(kind.value)
            for linked in (ticket_id, handoff_ticket_id):
                if linked is not None:
                    q.ticket_ids.add(str(linked))
        return list(questions.values())

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        # Question to question: both sides embedded the same way (symmetric).
        return await to_thread.run_sync(self.embedder.embed_documents, texts)

    async def analyze(self, organization_id: uuid.UUID) -> AnalysisResult:
        """Refresh every question's counts, place new questions (join a gap or form new
        ones), and recompute each gap's totals. Commits."""
        asked = {q.key: q for q in await self.collect(organization_id)}
        existing = {
            q.query_key: q
            for q in await self.session.scalars(
                select(KnowledgeGapQuestion).where(
                    KnowledgeGapQuestion.organization_id == organization_id
                )
            )
        }
        for key, row in existing.items():
            _apply(row, asked.get(key))

        new = sorted(
            (q for key, q in asked.items() if key not in existing),
            key=lambda q: (-q.asked, -(q.last_asked or _now()).timestamp(), q.key),
        )
        left = max(0, len(new) - self.max_new)
        new = new[: self.max_new]
        joined = created = 0
        if new:
            joined, created = await self._place(organization_id, new, list(existing.values()))
        await self.session.flush()
        await self._totals(organization_id)
        await self.session.commit()
        return AnalysisResult(len(asked), len(new), joined, created, left)

    async def _place(
        self,
        organization_id: uuid.UUID,
        new: list[AskedQuestion],
        existing: list[KnowledgeGapQuestion],
    ) -> tuple[int, int]:
        by_gap: dict[uuid.UUID, list[KnowledgeGapQuestion]] = {}
        for row in existing:
            by_gap.setdefault(row.gap_id, []).append(row)
        gap_ids = sorted(by_gap, key=str)
        members = [
            sorted(by_gap[g], key=lambda r: (-r.asked, r.query_key))[:CENTROID_QUESTIONS]
            for g in gap_ids
        ]
        flat = [row for rows in members for row in rows]
        vectors = await self._embed([r.query for r in flat] + [q.query for q in new])
        existing_vectors, new_vectors = vectors[: len(flat)], vectors[len(flat) :]

        centroids = []
        offset = 0
        for rows in members:
            part = existing_vectors[offset : offset + len(rows)]
            offset += len(rows)
            centroids.append(clustering.centroid(part, [r.asked + 1 for r in rows]))
        placement = clustering.assign(new_vectors, centroids, self.join_similarity)

        joined = 0
        rest: list[int] = []
        for index, gap_index in enumerate(placement):
            if gap_index is None:
                rest.append(index)
                continue
            self._add_question(organization_id, gap_ids[gap_index], new[index])
            joined += 1

        groups = clustering.cluster([new_vectors[i] for i in rest], self.cluster_similarity)
        for group in groups:
            indices = [rest[i] for i in group]
            label = clustering.representative(indices, [q.asked for q in new], new_vectors)
            gap = KnowledgeGap(
                id=uuid.uuid4(),
                organization_id=organization_id,
                label=new[label].query[:1000],
                status=GapStatus.OPEN,
                draft={},
                sources=[],
            )
            self.session.add(gap)
            await self.session.flush()
            for i in indices:
                self._add_question(organization_id, gap.id, new[i])
        return joined, len(groups)

    def _add_question(
        self, organization_id: uuid.UUID, gap_id: uuid.UUID, q: AskedQuestion
    ) -> None:
        row = KnowledgeGapQuestion(
            organization_id=organization_id, gap_id=gap_id, query_key=q.key, kinds=[], ticket_ids=[]
        )
        _apply(row, q)
        self.session.add(row)

    async def _totals(self, organization_id: uuid.UUID) -> None:
        rows = (
            await self.session.execute(
                select(
                    KnowledgeGapQuestion.gap_id,
                    KnowledgeGapQuestion.query,
                    KnowledgeGapQuestion.asked,
                    KnowledgeGapQuestion.last_asked,
                )
                .where(KnowledgeGapQuestion.organization_id == organization_id)
                .order_by(KnowledgeGapQuestion.query_key)  # ties name a gap the same way
            )
        ).all()
        stats: dict[uuid.UUID, list[Any]] = {}
        for gap_id, query, asked, last_asked in rows:
            s = stats.setdefault(gap_id, [0, 0, None, None, -1])
            s[0] += int(asked > 0)
            s[1] += asked
            if last_asked is not None and (s[2] is None or last_asked > s[2]):
                s[2] = last_asked
            if asked > s[4]:
                s[3], s[4] = query, asked
        for gap in await self.session.scalars(
            select(KnowledgeGap).where(KnowledgeGap.organization_id == organization_id)
        ):
            questions, asked, last_asked, label, _ = stats.get(gap.id, [0, 0, None, None, -1])
            gap.question_count, gap.asked, gap.last_asked = questions, asked, last_asked
            if label and gap.status == GapStatus.OPEN:
                gap.label = label[:1000]


def _apply(row: KnowledgeGapQuestion, q: AskedQuestion | None) -> None:
    """Copy a question's counts over the window (none: not asked in the window)."""
    if q is None:
        row.asked = 0
        return
    row.query = q.query
    row.asked = q.asked
    row.last_asked = q.last_asked
    row.last_run_id = q.last_run_id
    row.last_status = q.last_status
    row.kinds = sorted(set(row.kinds or []) | q.kinds)
    row.ticket_ids = sorted(set(row.ticket_ids or []) | q.ticket_ids)


# --- 2. Draft ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """A resolved ticket the draft may use."""

    ticket_id: uuid.UUID
    number: int
    subject: str
    via: str  # "linked" (the questions led to it) or "similar"
    similarity: float | None
    excerpt: TicketSource
    people: tuple[str, ...]  # the customer's and agents' names
    emails: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": str(self.ticket_id),
            "number": self.number,
            "subject": self.subject,
            "via": self.via,
            "similarity": None if self.similarity is None else round(self.similarity, 4),
        }


@dataclass(frozen=True)
class KbConflict:
    document_id: uuid.UUID
    document_title: str
    section: str | None
    chunk_id: uuid.UUID
    topic: str
    quote_draft: str
    quote_document: str
    explanation: str

    def to_json(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "section": self.section,
            "chunk_id": str(self.chunk_id),
            "topic": self.topic,
            "quote_draft": self.quote_draft,
            "quote_document": self.quote_document,
            "explanation": self.explanation,
        }


@dataclass
class DraftOutcome:
    status: DraftStatus
    sources: list[Source]
    draft: Draft | None = None
    conflicts: list[KbConflict] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)  # every passage judged
    private: drafting.PrivateTerms = field(default_factory=drafting.PrivateTerms)
    raw: str | None = None
    model: str | None = None
    latency_ms: int = 0
    error: str | None = None


def _content_terms(questions: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for q in questions:
        for t in terms(q):
            if re.fullmatch(r"[a-z0-9]+", t):
                seen.setdefault(t, None)
    return list(seen)[:30]


_SIMILAR_TICKETS = text(
    """
    WITH q AS (SELECT websearch_to_tsquery('english', :terms) AS query)
    SELECT t.id,
      ts_rank_cd(t.search_vector, q.query)
        + coalesce(sum(ts_rank_cd(m.search_vector, q.query)), 0) AS score
    FROM tickets t CROSS JOIN q
    LEFT JOIN ticket_messages m
      ON m.ticket_id = t.id AND NOT m.is_internal AND m.search_vector @@ q.query
    WHERE t.organization_id = :org AND t.status IN ('resolved', 'closed')
      AND (t.search_vector @@ q.query OR m.id IS NOT NULL)
    GROUP BY t.id, t.search_vector, q.query
    ORDER BY score DESC, t.id
    LIMIT :limit
    """
)


class GapDrafter:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMClient,
        embedder: Embedder,
        vectors: QdrantVectorStore,
        *,
        nli: NliModel | None = None,
        ticket_min_similarity: float | None = None,
        max_sources: int | None = None,
        conflict_passages: int | None = None,
        quote_contradiction: float | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.embedder = embedder
        self.vectors = vectors
        self.nli = nli
        self.quote_contradiction = (
            settings.gap_quote_contradiction if quote_contradiction is None else quote_contradiction
        )
        self.ticket_min_similarity = (
            settings.gap_ticket_min_similarity
            if ticket_min_similarity is None
            else ticket_min_similarity
        )
        self.max_sources = max_sources or settings.gap_max_sources
        self.conflict_passages = (
            settings.gap_conflict_passages if conflict_passages is None else conflict_passages
        )
        self.policy = ScanPolicy(
            require_grounding=True,
            min_confidence=settings.contradiction_min_confidence,
            both_orders=False,
        )

    async def questions(self, gap: KnowledgeGap) -> list[KnowledgeGapQuestion]:
        return list(
            await self.session.scalars(
                select(KnowledgeGapQuestion)
                .where(KnowledgeGapQuestion.gap_id == gap.id)
                .order_by(
                    KnowledgeGapQuestion.asked.desc(),
                    KnowledgeGapQuestion.last_asked.desc().nulls_last(),
                    KnowledgeGapQuestion.query_key,
                )
            )
        )

    async def sources(
        self, organization_id: uuid.UUID, questions: Sequence[KnowledgeGapQuestion]
    ) -> list[Source]:
        """Linked tickets first, then similar ones, up to ``max_sources``. Only resolved
        or closed tickets with a public agent reply."""
        linked_ids: list[uuid.UUID] = []
        for q in questions:
            for ticket_id in q.ticket_ids or []:
                parsed = uuid.UUID(ticket_id)
                if parsed not in linked_ids:
                    linked_ids.append(parsed)
        texts = [q.query for q in questions[:PROMPT_QUESTIONS]]
        candidate_ids: list[uuid.UUID] = []
        query_terms = _content_terms(texts)
        if query_terms:
            candidate_ids = [
                row.id
                for row in (
                    await self.session.execute(
                        _SIMILAR_TICKETS,
                        {
                            "terms": " or ".join(query_terms),
                            "org": organization_id,
                            "limit": settings.gap_ticket_candidates,
                        },
                    )
                ).all()
                if row.id not in linked_ids
            ]
        excerpts = await self._excerpts(organization_id, linked_ids + candidate_ids)

        sources = [replace(excerpts[t], via="linked") for t in linked_ids if t in excerpts][
            : self.max_sources
        ]
        similar = [excerpts[t] for t in candidate_ids if t in excerpts]
        room = self.max_sources - len(sources)
        if similar and room > 0 and texts:
            vectors = await to_thread.run_sync(
                self.embedder.embed_documents,
                texts + [f"{s.subject}\n{s.excerpt.customer_text}" for s in similar],
            )
            question_vectors, ticket_vectors = vectors[: len(texts)], vectors[len(texts) :]
            scored = [
                (max(clustering.cosine(q, t) for q in question_vectors), s)
                for s, t in zip(similar, ticket_vectors, strict=True)
            ]
            scored = [(score, s) for score, s in scored if score >= self.ticket_min_similarity]
            scored.sort(key=lambda item: (-item[0], item[1].number))
            sources += [replace(s, similarity=score) for score, s in scored[:room]]
        return sources

    async def _excerpts(
        self, organization_id: uuid.UUID, ticket_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Source]:
        if not ticket_ids:
            return {}
        tickets = {
            t.id: t
            for t in await self.session.scalars(
                select(Ticket).where(
                    Ticket.organization_id == organization_id,
                    Ticket.id.in_(ticket_ids),
                    Ticket.status.in_(SOURCE_STATUSES),
                )
            )
        }
        if not tickets:
            return {}
        messages = (
            await self.session.execute(
                select(
                    TicketMessage.ticket_id,
                    TicketMessage.author_type,
                    TicketMessage.body,
                    User.full_name,
                    User.email,
                )
                .outerjoin(User, User.id == TicketMessage.author_id)
                .where(
                    TicketMessage.ticket_id.in_(list(tickets)),
                    TicketMessage.is_internal.is_(False),
                )
                .order_by(TicketMessage.ticket_id, TicketMessage.seq)
            )
        ).all()
        customers = {
            c.id: c
            for c in await self.session.scalars(
                select(Customer).where(Customer.id.in_({t.customer_id for t in tickets.values()}))
            )
        }
        found: dict[uuid.UUID, Source] = {}
        for ticket_id, ticket in tickets.items():
            rows = [m for m in messages if m.ticket_id == ticket_id]
            # The opening message is the customer's question even when staff logged the
            # ticket for them (then its author is an agent): it is never a reply.
            first = rows[0].body if rows else ""
            replies = [m.body for m in rows[1:] if m.author_type == MessageAuthorType.AGENT]
            if not replies:
                continue
            customer = customers.get(ticket.customer_id)
            people = [m.full_name for m in rows if m.full_name]
            emails = [m.email for m in rows if m.email]
            if customer is not None:
                people.append(customer.name)
                if customer.email:
                    emails.append(customer.email)
            found[ticket_id] = Source(
                ticket_id=ticket_id,
                number=ticket.number,
                subject=ticket.subject,
                via="similar",
                similarity=None,
                excerpt=TicketSource(
                    number=ticket.number,
                    subject=ticket.subject,
                    customer_text=first,
                    replies="\n\n".join(replies),
                ),
                people=tuple(dict.fromkeys(people)),
                emails=tuple(dict.fromkeys(emails)),
            )
        return found

    async def draft(self, gap: KnowledgeGap) -> DraftOutcome:
        """Find sources, ask the model, check the draft. Doesn't record anything."""
        questions = await self.questions(gap)
        sources = await self.sources(gap.organization_id, questions)
        await self.session.commit()  # don't hold a transaction across model calls
        if not sources:
            return DraftOutcome(DraftStatus.NO_SOURCES, sources)
        private = drafting.private_terms(
            [p for s in sources for p in s.people], [e for s in sources for e in s.emails]
        )
        started = time.perf_counter()
        outcome = DraftOutcome(DraftStatus.FAILED, sources, private=private)
        try:
            completion = await self.llm.complete(
                Prompt(
                    drafting.SYSTEM,
                    drafting.prompt_user(
                        [q.query for q in questions[:PROMPT_QUESTIONS]],
                        [s.excerpt for s in sources],
                    ),
                    drafting.SCHEMA,
                )
            )
            outcome.raw = completion.text
            outcome.model = completion.model
            parsed = drafting.parse(completion.text)
            if parsed is None:
                outcome.error = "The model's reply wasn't valid JSON."
            else:
                checked = drafting.verify(parsed, [s.excerpt for s in sources], private)
                checked = await self._check_quotes(checked)
                if checked.body():
                    await self._check_conflicts(gap, checked, outcome)
                    checked = drafting.mark_conflicts(
                        checked, [c.quote_draft for c in outcome.conflicts]
                    )
                outcome.draft = checked
                outcome.status = (
                    DraftStatus.DRAFTED if checked.included else DraftStatus.NOT_ANSWERED
                )
        except LLMError as exc:
            outcome.error = str(exc)
            outcome.status = DraftStatus.FAILED
        outcome.latency_ms = round((time.perf_counter() - started) * 1000)
        return outcome

    async def _check_quotes(self, checked: Draft) -> Draft:
        """Leave out statements their own quote contradicts (no model: skipped)."""
        nli = self.nli
        if nli is None or self.quote_contradiction <= 0 or not checked.statements:
            return checked
        scores = await to_thread.run_sync(nli.score, drafting.quote_pairs(checked))
        return drafting.mark_contradicted(
            checked, [s.contradiction for s in scores], self.quote_contradiction
        )

    async def _check_conflicts(
        self, gap: KnowledgeGap, checked: Draft, outcome: DraftOutcome
    ) -> None:
        """Judge the draft against its nearest knowledge-base passages. A conflict the
        judge can quote from both sides is reported, and the draft's side is left out."""
        if self.conflict_passages <= 0:
            return
        body = checked.body()
        [vector] = await to_thread.run_sync(self.embedder.embed_documents, [body])
        hits = await self.vectors.search(
            gap.organization_id, vector, limit=self.conflict_passages + 2
        )
        if not hits:
            return
        rows = (
            await self.session.execute(
                select(
                    DocumentChunk.id,
                    DocumentChunk.document_id,
                    DocumentChunk.section_heading,
                    DocumentChunk.content,
                    Document.title,
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.id.in_([h.chunk_id for h in hits]),
                    Document.indexed_version.is_not(None),
                )
            )
        ).all()
        await self.session.commit()
        by_id = {row.id: row for row in rows}
        passages = [
            by_id[h.chunk_id]
            for h in hits
            if h.chunk_id in by_id and by_id[h.chunk_id].document_id != gap.document_id
        ][: self.conflict_passages]
        article = PassageText(f"Draft article: {checked.title or gap.label}", None, body)
        for passage in passages:
            check: dict[str, Any] = {
                "document_id": str(passage.document_id),
                "chunk_id": str(passage.id),
                "verdict": None,
                "flagged": False,
            }
            outcome.checks.append(check)
            try:
                completion = await self.llm.complete(
                    Prompt(
                        judge.SYSTEM,
                        judge.prompt_user(
                            article,
                            PassageText(passage.title, passage.section_heading, passage.content),
                        ),
                        judge.SCHEMA,
                    )
                )
            except LLMError as exc:  # the draft stands; this passage wasn't checked
                check["error"] = str(exc)[:500]
                continue
            parsed = judge.parse(completion.text)
            judgement = judge.verify(parsed, body, passage.content) if parsed else None
            flagged = judge.flagged(judgement, self.policy)
            check["verdict"] = judgement.verdict if judgement else None
            check["flagged"] = flagged
            if flagged and judgement is not None:
                outcome.conflicts += [
                    KbConflict(
                        document_id=passage.document_id,
                        document_title=passage.title,
                        section=passage.section_heading,
                        chunk_id=passage.id,
                        topic=c.topic,
                        quote_draft=c.quote_a,
                        quote_document=c.quote_b,
                        explanation=c.explanation,
                    )
                    for c in judgement.grounded_conflicts
                ]

    async def record(self, gap_id: uuid.UUID, outcome: DraftOutcome) -> KnowledgeGap | None:
        gap = await self.session.get(KnowledgeGap, gap_id, populate_existing=True)
        if gap is None:
            return None
        draft = outcome.draft
        gap.draft_status = outcome.status
        gap.draft_title = (draft.title if draft else None) or None
        gap.draft_body = draft.body() if draft else None
        gap.draft = {
            "verdict": draft.verdict if draft else None,
            "statements": [s.to_json() for s in draft.statements] if draft else [],
            "conflicts": [c.to_json() for c in outcome.conflicts],
            "conflict_checks": outcome.checks,
            "private_terms": outcome.private.to_json(),
            "raw": (outcome.raw or "")[:8000] or None,
        }
        gap.sources = [s.to_json() for s in outcome.sources]
        gap.draft_model = outcome.model or getattr(self.llm, "model", None)
        gap.draft_latency_ms = outcome.latency_ms
        gap.draft_error = outcome.error
        gap.drafted_at = _now()
        await self.session.commit()
        return gap


# --- 4. Recheck ----------------------------------------------------------------------------


async def recheck_gap(
    session: AsyncSession,
    gap: KnowledgeGap,
    member: OrganizationMember,
    user: User,
    *,
    llm: LLMClient,
    embedder: Embedder,
    vectors: QdrantVectorStore,
    reranker: Reranker,
    nli: NliModel | None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Ask a published gap's questions again (staff pipeline, assessed policy) and
    record how many are answered now, and how many from the article. Commits."""
    from app.schemas.answers import AskRequest
    from app.services.answering import AnswerService
    from app.services.retrieval import RetrievalService

    questions = (
        await session.scalars(
            select(KnowledgeGapQuestion)
            .where(KnowledgeGapQuestion.gap_id == gap.id)
            .order_by(KnowledgeGapQuestion.asked.desc(), KnowledgeGapQuestion.query_key)
            .limit(limit or settings.gap_recheck_questions)
        )
    ).all()
    document_id = gap.document_id
    service = AnswerService(
        session,
        retrieval=RetrievalService(
            session, embedder=embedder, vector_store=vectors, reranker=reranker
        ),
        llm=llm,
        nli=nli,
    )
    results = []
    for q in questions:
        query, before = q.query, q.last_status
        run, _ = await service.answer_question(member, user, AskRequest(query=query[:500]))
        run.config = {**(run.config or {}), "origin": GAP_CHECK_ORIGIN}
        await session.commit()
        answer = run.answer
        cited = bool(
            answer is not None
            and document_id is not None
            and any(c.document_id == document_id for c in answer.citations)
        )
        results.append(
            {
                "query": query,
                "before": before,
                "after": answer.status.value if answer else None,
                "run_id": str(run.id),
                "cited_article": cited,
            }
        )
    summary = {
        "total": len(results),
        "answered": sum(r["after"] == AnswerStatus.ANSWERED.value for r in results),
        "cited_article": sum(r["cited_article"] for r in results),
        "questions": results,
    }
    fresh = await session.get(KnowledgeGap, gap.id, populate_existing=True)
    if fresh is not None:
        fresh.recheck = summary
        fresh.rechecked_at = _now()
        await session.commit()
    return summary


# --- Jobs ----------------------------------------------------------------------------------


async def _close_stale_jobs(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        update(KnowledgeGapJob)
        .where(
            KnowledgeGapJob.organization_id == organization_id,
            KnowledgeGapJob.status == GapJobStatus.RUNNING,
            KnowledgeGapJob.started_at < _now() - STALE_JOB,
        )
        .values(status=GapJobStatus.FAILED, error="Interrupted", finished_at=func.now())
    )


async def start_job(
    session: AsyncSession,
    organization_id: uuid.UUID,
    kind: GapJobKind,
    *,
    gap_id: uuid.UUID | None = None,
    requested_by_id: uuid.UUID | None = None,
) -> KnowledgeGapJob | None:
    """Record a running job, or None if one is already running."""
    await _close_stale_jobs(session, organization_id)
    job = KnowledgeGapJob(
        organization_id=organization_id,
        kind=kind,
        gap_id=gap_id,
        requested_by_id=requested_by_id,
        status=GapJobStatus.RUNNING,
        result={},
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return job


async def _get_gap(
    session: AsyncSession, member: OrganizationMember, gap_id: uuid.UUID, *, lock: bool = False
) -> KnowledgeGap:
    query = select(KnowledgeGap).where(
        KnowledgeGap.id == gap_id, KnowledgeGap.organization_id == member.organization_id
    )
    gap = await session.scalar(query.with_for_update() if lock else query)
    if gap is None:
        raise NotFoundError("Gap not found")
    return gap


async def request_job(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    queue: GapQueue,
    kind: GapJobKind,
    gap_id: uuid.UUID | None = None,
) -> KnowledgeGapJob:
    """An admin starts a job. 409 ``job_running`` if one is running; drafting needs an
    open gap, a recheck a published one whose article is indexed."""
    require_role(member, OrgRole.ADMIN)
    if gap_id is not None:
        gap = await _get_gap(session, member, gap_id)
        if kind == GapJobKind.DRAFT and gap.status != GapStatus.OPEN:
            raise ConflictError("Only open gaps can be drafted.", code="gap_not_open")
        if kind == GapJobKind.RECHECK:
            document = await session.get(Document, gap.document_id) if gap.document_id else None
            if gap.status != GapStatus.PUBLISHED or document is None:
                raise ConflictError("The gap has no published article.", code="not_published")
            if document.indexed_version is None:
                raise ConflictError(
                    "The article isn't indexed yet. Try again when it is.",
                    code="article_not_indexed",
                )
    job = await start_job(
        session, member.organization_id, kind, gap_id=gap_id, requested_by_id=user.id
    )
    if job is None:
        raise ConflictError("A knowledge-gap job is already running.", code="job_running")
    try:
        await queue.enqueue(job.id)
    except QueueUnavailableError as exc:
        logger.warning("Couldn't queue knowledge-gap job %s: %s", job.id, exc)
        job.status = GapJobStatus.FAILED
        job.error = "The job queue is unavailable."
        job.finished_at = _now()
        await session.commit()
        raise ServiceUnavailableError(
            "The job couldn't be started because the job queue is unavailable. Try again."
        ) from exc
    return job


@dataclass(frozen=True)
class JobTools:
    llm: LLMClient
    embedder: Embedder
    vectors: QdrantVectorStore
    reranker: Reranker | None = None
    nli: NliModel | None = None


async def run_job(
    session: AsyncSession, job_id: uuid.UUID, tools: JobTools
) -> KnowledgeGapJob | None:
    """The job: run a recorded job (as a system operation) and record its outcome."""
    await set_rls_context(session, bypass=True)
    job = await session.get(KnowledgeGapJob, job_id)
    if job is None or job.status != GapJobStatus.RUNNING:
        return None
    organization_id, kind, gap_id = job.organization_id, job.kind, job.gap_id
    try:
        result = await _run(session, job, tools)
        job = await _refresh_job(session, job_id)
        job.result = result
        job.status = GapJobStatus.FINISHED
    except Exception as exc:
        logger.exception("Knowledge-gap job %s (%s) failed", job_id, kind)
        await session.rollback()
        job = await _refresh_job(session, job_id)
        job.status = GapJobStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"[:500]
    job.finished_at = _now()
    await session.commit()
    logger.info("Knowledge-gap job %s %s org=%s gap=%s", kind, job.status, organization_id, gap_id)
    return job


async def _refresh_job(session: AsyncSession, job_id: uuid.UUID) -> KnowledgeGapJob:
    job = await session.get(KnowledgeGapJob, job_id, populate_existing=True)
    if job is None:  # the organization was deleted meanwhile
        raise NotFoundError("Job not found")
    return job


async def _run(session: AsyncSession, job: KnowledgeGapJob, tools: JobTools) -> dict[str, Any]:
    if job.kind == GapJobKind.ANALYZE:
        result = await GapAnalyzer(session, tools.embedder).analyze(job.organization_id)
        return result.to_json()
    gap = await session.get(KnowledgeGap, job.gap_id) if job.gap_id else None
    if gap is None:
        raise NotFoundError("Gap not found")
    if job.kind == GapJobKind.DRAFT:
        drafter = GapDrafter(session, tools.llm, tools.embedder, tools.vectors, nli=tools.nli)
        outcome = await drafter.draft(gap)
        await drafter.record(gap.id, outcome)
        draft = outcome.draft
        return {
            "status": outcome.status.value,
            "sources": len(outcome.sources),
            "statements": len(draft.statements) if draft else 0,
            "included": len(draft.included) if draft else 0,
            "conflicts": len(outcome.conflicts),
        }
    # Recheck: as the admin who asked (their organization role still applies).
    member = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == job.organization_id,
            OrganizationMember.user_id == job.requested_by_id,
        )
    )
    user = await session.get(User, job.requested_by_id) if job.requested_by_id else None
    if member is None or user is None or member.role not in STAFF_ROLES:
        raise ConflictError("The person who asked for the recheck is no longer staff here.")
    if tools.reranker is None:
        raise RuntimeError("A recheck needs the reranker")
    summary = await recheck_gap(
        session,
        gap,
        member,
        user,
        llm=tools.llm,
        embedder=tools.embedder,
        vectors=tools.vectors,
        reranker=tools.reranker,
        nli=tools.nli,
    )
    return {"total": summary["total"], "answered": summary["answered"]}


async def run_gap_job(job_id: uuid.UUID) -> str | None:
    """Celery entry point."""
    from app.core.knowledge import (
        get_embedder,
        get_nli_model,
        get_reranker,
        new_qdrant_client,
        vector_store_for,
    )
    from app.core.llm import get_llm_client
    from app.workers.context import with_worker_context

    async def job(session: AsyncSession, *_: object) -> str | None:
        client = new_qdrant_client()
        try:
            embedder = get_embedder()
            done = await run_job(
                session,
                job_id,
                JobTools(
                    llm=get_llm_client(),
                    embedder=embedder,
                    vectors=vector_store_for(client, embedder),
                    reranker=get_reranker(),
                    nli=get_nli_model(),
                ),
            )
        finally:
            await client.close()
        return done.status.value if done else None

    return await with_worker_context(job)


# --- Reading, reviewing and publishing -----------------------------------------------------


@dataclass(frozen=True)
class GapRow:
    gap: KnowledgeGap
    reviewer: User | None
    publisher: User | None
    document: Document | None
    questions: list[KnowledgeGapQuestion] | None = None  # the detail view only
    asked_since_published: int = 0


async def _rows(
    session: AsyncSession, gaps: Sequence[KnowledgeGap], *, with_questions: bool = False
) -> list[GapRow]:
    user_ids = {g.reviewed_by_id for g in gaps if g.reviewed_by_id} | {
        g.published_by_id for g in gaps if g.published_by_id
    }
    users = (
        {u.id: u for u in await session.scalars(select(User).where(User.id.in_(user_ids)))}
        if user_ids
        else {}
    )
    document_ids = {g.document_id for g in gaps if g.document_id}
    documents = (
        {
            d.id: d
            for d in await session.scalars(select(Document).where(Document.id.in_(document_ids)))
        }
        if document_ids
        else {}
    )
    questions: dict[uuid.UUID, list[KnowledgeGapQuestion]] = {}
    if gaps:
        for q in await session.scalars(
            select(KnowledgeGapQuestion)
            .where(KnowledgeGapQuestion.gap_id.in_([g.id for g in gaps]))
            .order_by(
                KnowledgeGapQuestion.asked.desc(),
                KnowledgeGapQuestion.last_asked.desc().nulls_last(),
                KnowledgeGapQuestion.query_key,
            )
        ):
            questions.setdefault(q.gap_id, []).append(q)
    rows = []
    for g in gaps:
        members = questions.get(g.id, [])
        since = (
            sum(
                1
                for q in members
                if g.published_at and q.last_asked and q.last_asked > g.published_at
            )
            if g.status == GapStatus.PUBLISHED
            else 0
        )
        rows.append(
            GapRow(
                gap=g,
                reviewer=users.get(g.reviewed_by_id) if g.reviewed_by_id else None,
                publisher=users.get(g.published_by_id) if g.published_by_id else None,
                document=documents.get(g.document_id) if g.document_id else None,
                questions=members if with_questions else members[:3],
                asked_since_published=since,
            )
        )
    return rows


async def list_gaps(
    session: AsyncSession,
    member: OrganizationMember,
    *,
    status: GapStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[GapRow], int]:
    require_role(member, *STAFF_ROLES)
    query = select(KnowledgeGap).where(KnowledgeGap.organization_id == member.organization_id)
    if status is not None:
        query = query.where(KnowledgeGap.status == status)
    gaps = (
        await session.scalars(
            query.order_by(
                KnowledgeGap.asked.desc(),
                KnowledgeGap.last_asked.desc().nulls_last(),
                KnowledgeGap.id,
            )
            .limit(min(limit, MAX_PAGE))
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    return await _rows(session, gaps), int(total or 0)


async def get_gap(session: AsyncSession, member: OrganizationMember, gap_id: uuid.UUID) -> GapRow:
    require_role(member, *STAFF_ROLES)
    gap = await _get_gap(session, member, gap_id)
    return (await _rows(session, [gap], with_questions=True))[0]


async def review_gap(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    gap_id: uuid.UUID,
    status: GapStatus,
    note: str | None,
) -> GapRow:
    """An admin dismisses or reopens a gap (audited). Publishing has its own action."""
    require_role(member, OrgRole.ADMIN)
    if status == GapStatus.PUBLISHED:
        raise ConflictError("Publish a gap with its article.", code="publish_with_article")
    gap = await _get_gap(session, member, gap_id, lock=True)
    if gap.status == GapStatus.PUBLISHED:
        raise ConflictError("This gap's article is published.", code="gap_published")
    before = gap.status
    if before != status:
        gap.status = status
        gap.reviewed_by_id = user.id
        gap.reviewed_at = _now()
        gap.note = (note or "").strip()[:1000] or None
        await audit.record(
            session,
            organization_id=member.organization_id,
            actor_id=user.id,
            entity_type="knowledge_gap",
            entity_id=gap.id,
            action="knowledge_gap.dismissed"
            if status == GapStatus.DISMISSED
            else "knowledge_gap.reopened",
            summary=f"Knowledge gap {'dismissed' if status == GapStatus.DISMISSED else 'reopened'}"
            f": {gap.label[:120]}",
            changes={"status": {"from": before.value, "to": status.value}},
        )
        await session.commit()
    return await get_gap(session, member, gap.id)


_SLUG = re.compile(r"[^a-z0-9]+")


def article_filename(title: str) -> str:
    slug = _SLUG.sub("-", title.casefold()).strip("-")[:80].strip("-")
    return f"{slug or 'article'}.md"


async def publish_gap(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    gap_id: uuid.UUID,
    *,
    title: str,
    body: str,
    visibility: DocumentVisibility,
    storage: ObjectStorage,
    queue: IngestionQueue,
) -> GapRow:
    """Publish the (edited) article as a Markdown document and queue its indexing. The
    document is an ordinary one from then on: it can be edited, re-versioned, made
    public or deleted like any other."""
    from app.services.documents import DocumentService

    require_role(member, OrgRole.ADMIN)
    gap = await _get_gap(session, member, gap_id)
    if gap.status != GapStatus.OPEN:
        raise ConflictError("Only open gaps can be published.", code="gap_not_open")
    documents = DocumentService(session, storage=storage, queue=queue)
    content = drafting.article_markdown(title, body).encode("utf-8")
    document = await documents.upload(
        member, user, filename=article_filename(title), content=content, title=title.strip()
    )
    if visibility != DocumentVisibility.INTERNAL:
        document = await documents.update(
            member, document.id, DocumentUpdate(visibility=visibility)
        )
    gap = await _get_gap(session, member, gap_id, lock=True)
    if gap.status != GapStatus.OPEN:  # published concurrently: keep the document, report it
        raise ConflictError("This gap was published meanwhile.", code="gap_not_open")
    gap.status = GapStatus.PUBLISHED
    gap.document_id = document.id
    gap.published_by_id = user.id
    gap.published_at = _now()
    edited = (gap.draft_body or "").strip() != body.strip() or (gap.draft_title or "") != title
    await audit.record(
        session,
        organization_id=member.organization_id,
        actor_id=user.id,
        entity_type="knowledge_gap",
        entity_id=gap.id,
        action="knowledge_gap.published",
        summary=f"Published the article {title.strip()[:120]} for a knowledge gap",
        changes={
            "document_id": {"from": None, "to": str(document.id)},
            "visibility": {"from": None, "to": visibility.value},
            "edited": {"from": None, "to": edited},
        },
    )
    await session.commit()
    await documents.start_ingestion(document)
    return await get_gap(session, member, gap.id)


@dataclass(frozen=True)
class GapSummary:
    open: int
    dismissed: int
    published: int
    drafted: int  # open gaps with a draft ready
    questions: int  # distinct unanswered questions in the window
    latest_job: KnowledgeGapJob | None
    latest_analysis: KnowledgeGapJob | None


async def summary(session: AsyncSession, member: OrganizationMember) -> GapSummary:
    require_role(member, *STAFF_ROLES)
    org = member.organization_id
    counts = dict(
        (
            await session.execute(
                select(KnowledgeGap.status, func.count())
                .where(KnowledgeGap.organization_id == org)
                .group_by(KnowledgeGap.status)
            )
        ).all()
    )
    drafted = await session.scalar(
        select(func.count()).where(
            KnowledgeGap.organization_id == org,
            KnowledgeGap.status == GapStatus.OPEN,
            KnowledgeGap.draft_status == DraftStatus.DRAFTED,
        )
    )
    questions = await session.scalar(
        select(func.count()).where(
            KnowledgeGapQuestion.organization_id == org, KnowledgeGapQuestion.asked > 0
        )
    )

    async def latest(kind: GapJobKind | None) -> KnowledgeGapJob | None:
        query = select(KnowledgeGapJob).where(KnowledgeGapJob.organization_id == org)
        if kind is not None:
            query = query.where(KnowledgeGapJob.kind == kind)
        return await session.scalar(
            query.order_by(
                KnowledgeGapJob.started_at.desc(),
                KnowledgeGapJob.finished_at.desc().nulls_first(),
            ).limit(1)
        )

    return GapSummary(
        open=counts.get(GapStatus.OPEN, 0),
        dismissed=counts.get(GapStatus.DISMISSED, 0),
        published=counts.get(GapStatus.PUBLISHED, 0),
        drafted=int(drafted or 0),
        questions=int(questions or 0),
        latest_job=await latest(None),
        latest_analysis=await latest(GapJobKind.ANALYZE),
    )


async def reset_organization(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Delete every gap, question and job of an organization (evaluations start clean)."""
    for model in (KnowledgeGapJob, KnowledgeGapQuestion, KnowledgeGap):
        await session.execute(delete(model).where(model.organization_id == organization_id))
    await session.commit()
