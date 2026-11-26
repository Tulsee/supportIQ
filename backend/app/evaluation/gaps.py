"""Evaluating knowledge gaps to articles (Module 13, experiments G1-G3).

The Quillon documents are indexed into their own evaluation organization, with 23
resolved tickets (``datasets/knowledge_gaps/tickets_v1.jsonl``): 14 that answer (or, for
two decoys, fail to answer) a question the documents don't cover, and 9 distractors.
The 34 "asked" questions of 12 gap topics (``topics_v1.jsonl``) go through the staff
answer pipeline exactly as the Ask page sends them; those it can't answer are the gaps.

    G1  Grouping. The unanswered questions, grouped at a range of similarity thresholds
        (average linkage, as the first analysis does): pairwise precision, recall and F1
        against the gold topics, and B-cubed F1. Thresholds are chosen on the dev topics
        and reported on the test topics; the live analysis uses the dev choice.
    G2  Drafting. Each gap is drafted from the tickets exactly as the worker does:
        source recall and precision against the gold tickets; drafts made for answerable
        topics and refused for the decoys; key facts in the body; statements left out by
        each check; customers' details in a body (must be 0); the planted outdated claim
        (the 2024 FAQ's 60-day return window, repeated in an agent's reply) in a body
        (must be 0) and whether the conflict check flagged it.
    G3  Effect. Every draft is published unedited (the worst case: no admin review) and
        indexed. Held-out paraphrases, never seen by the analysis, are asked before and
        after: answered, and answered with every key fact; exact McNemar on the pairs.
        The decoys' held-out questions must still not be answered. Each published gap is
        also rechecked as in the product (its own questions asked again). With
        ``regression``, the Quillon benchmark's test split is asked before and after
        publishing, to see whether the new articles changed any other answer.

Held-out and regression questions are recorded as runs with an ``origin`` (probes), so
the analysis never collects them as gaps. Model replies are cached (JSON lines, keyed by
the exact prompt), so a run can stop and resume and a rerun costs no model time.
"""

import hashlib
import json
import logging
import math
import time
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import Embedder
from ai_engine.evaluation.dataset import Question
from ai_engine.evaluation.metrics import QuestionOutcome, answer_metrics, normalize, rate
from ai_engine.evidence.nli import NliModel
from ai_engine.gaps import cluster as clustering
from ai_engine.gaps import draft as drafting
from ai_engine.generation.llm import Completion, LLMClient, Prompt
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.database import rls_bypass
from app.core.storage import ObjectStorage
from app.evaluation.runner import Workspace, outcome_of
from app.models import (
    AIRun,
    Customer,
    DocumentVisibility,
    DraftStatus,
    GapStatus,
    KnowledgeGap,
    KnowledgeGapQuestion,
    MessageAuthorType,
    Ticket,
    TicketCounter,
    TicketMessage,
    TicketPriority,
    TicketStatus,
)
from app.repositories.tickets import TicketRepository
from app.schemas.answers import AskRequest
from app.services.answering import AnswerService
from app.services.ingestion import IngestionLimits, IngestionService, Outcome
from app.services.knowledge_gaps import (
    GAP_STATUSES,
    GapAnalyzer,
    GapDrafter,
    publish_gap,
    recheck_gap,
    reset_organization,
)
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)

PROBE_ORIGIN = "evaluation_probe"
THRESHOLDS = [round(0.5 + 0.05 * i, 2) for i in range(10)]  # 0.50 ... 0.95
GAP_STATUS_VALUES = {s.value for s in GAP_STATUSES}


# --- Data ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class HeldOut:
    question: str
    key_facts: list[list[str]]


@dataclass(frozen=True)
class Topic:
    id: str
    split: str
    answerable: bool
    topic: str
    asked: list[str]
    held_out: list[HeldOut]
    sources: list[str]
    draft_facts: list[list[str]]
    outdated: list[str]
    notes: str = ""


@dataclass(frozen=True)
class TicketRow:
    id: str
    topic: str | None
    subject: str
    customer: str
    email: str
    message: str
    reply: str


def load_topics(path: Path) -> list[Topic]:
    topics = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["held_out"] = [HeldOut(**h) for h in row["held_out"]]
            topics.append(Topic(**row))
    return topics


def load_tickets(path: Path) -> list[TicketRow]:
    return [
        TicketRow(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- The model, cached ---------------------------------------------------------------------


class CachingLLM:
    """Replays replies from a JSON-lines cache keyed by the exact prompt and the model,
    and records new ones. Everything else is passed through to the real client."""

    def __init__(self, llm: LLMClient, path: Path) -> None:
        self._llm = llm
        self._path = path
        self._cache: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._cache[row["key"]] = row
        self.hits = 0
        self.misses = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

    def _key(self, prompt: Prompt) -> str:
        payload = json.dumps(
            [str(getattr(self._llm, "model", "")), prompt.system, prompt.user, prompt.json_schema],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def complete(self, prompt: Prompt) -> Completion:
        key = self._key(prompt)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return Completion(
                text=cached["text"],
                model=cached["model"],
                stop_reason=cached.get("stop_reason"),
                input_tokens=cached.get("input_tokens"),
                output_tokens=cached.get("output_tokens"),
                model_version=cached.get("model_version"),
                timings_ms={"total": float(cached.get("latency_ms", 0.0))},
            )
        started = time.perf_counter()
        completion = await self._llm.complete(prompt)
        self.misses += 1
        row = {
            "key": key,
            "text": completion.text,
            "model": completion.model,
            "stop_reason": completion.stop_reason,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "model_version": completion.model_version,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        self._cache[key] = row
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        return completion


# --- Scoring ------------------------------------------------------------------------------


def facts_found(text: str | None, facts: Sequence[Sequence[str]]) -> list[bool]:
    haystack = normalize(text or "")
    return [any(normalize(p) in haystack for p in phrasings) for phrasings in facts]


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from the discordant counts (b: only before
    right, c: only after right)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def pairwise(labels_true: Sequence[str], labels_pred: Sequence[int]) -> dict[str, Any]:
    """Pairwise precision, recall and F1 of a clustering, and B-cubed F1."""
    n = len(labels_true)
    tp = fp = fn = 0
    for i in range(n):
        for j in range(i + 1, n):
            same_true = labels_true[i] == labels_true[j]
            same_pred = labels_pred[i] == labels_pred[j]
            tp += same_true and same_pred
            fp += same_pred and not same_true
            fn += same_true and not same_pred
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    b_p = b_r = 0.0
    for i in range(n):
        pred = [j for j in range(n) if labels_pred[j] == labels_pred[i]]
        true = [j for j in range(n) if labels_true[j] == labels_true[i]]
        both = sum(1 for j in pred if labels_true[j] == labels_true[i])
        b_p += both / len(pred)
        b_r += both / len(true)
    b_p, b_r = (b_p / n, b_r / n) if n else (1.0, 1.0)
    return {
        "pairs_precision": round(precision, 4),
        "pairs_recall": round(recall, 4),
        "pairs_f1": round(f1, 4),
        "bcubed_f1": round(2 * b_p * b_r / (b_p + b_r), 4) if b_p + b_r else 0.0,
        "clusters": len(set(labels_pred)),
        "topics": len(set(labels_true)),
        "questions": n,
    }


def sweep(
    questions: Sequence[str], topics: Sequence[str], vectors: Sequence[Sequence[float]]
) -> list[dict[str, Any]]:
    rows = []
    for threshold in THRESHOLDS:
        groups = clustering.cluster(vectors, threshold)
        labels = [0] * len(questions)
        for g, members in enumerate(groups):
            for i in members:
                labels[i] = g
        rows.append({"threshold": threshold, **pairwise(topics, labels)})
    return rows


def choose_threshold(rows: Sequence[dict[str, Any]]) -> float:
    """Best pairwise F1; ties go to the higher (more conservative) threshold."""
    best = max(rows, key=lambda r: (r["pairs_f1"], r["threshold"]))
    return float(best["threshold"])


# --- The run -------------------------------------------------------------------------------


class _NoQueue:
    """Publishing queues indexing; the evaluation indexes in-process instead."""

    async def enqueue(self, *_: object) -> None:
        return None


@dataclass
class GapRun:
    workspace: Workspace
    session: AsyncSession
    llm: LLMClient
    embedder: Embedder
    vectors: QdrantVectorStore
    reranker: Reranker
    storage: ObjectStorage
    limits: IngestionLimits
    nli: NliModel | None = None
    progress: Callable[[str], None] = print
    ticket_ids: dict[str, uuid.UUID] = field(default_factory=dict)  # dataset id -> ticket

    @property
    def organization_id(self) -> uuid.UUID:
        return self.workspace.organization.id

    async def reset(self) -> None:
        """Start clean: no runs, gaps, tickets or customers in the evaluation
        organization (the documents are kept; stale articles are removed by prepare)."""
        org = self.organization_id
        async with rls_bypass(self.session):
            await self.session.execute(delete(AIRun).where(AIRun.organization_id == org))
            await reset_organization(self.session, org)
            await self.session.execute(delete(Ticket).where(Ticket.organization_id == org))
            await self.session.execute(delete(Customer).where(Customer.organization_id == org))
            await self.session.execute(
                delete(TicketCounter).where(TicketCounter.organization_id == org)
            )
            await self.session.commit()

    async def seed_tickets(self, tickets: Sequence[TicketRow]) -> None:
        """Resolved tickets: the customer's message, then the agent's public reply."""
        org = self.organization_id
        agent = self.workspace.user
        repository = TicketRepository(self.session)
        async with rls_bypass(self.session):
            for row in tickets:
                customer = Customer(organization_id=org, name=row.customer, email=row.email)
                self.session.add(customer)
                await self.session.flush()
                ticket = Ticket(
                    organization_id=org,
                    number=await repository.next_number(org),
                    customer_id=customer.id,
                    subject=row.subject,
                    status=TicketStatus.RESOLVED,
                    priority=TicketPriority.NORMAL,
                    assignee_id=agent.id,
                    resolved_at=datetime.now(UTC),
                )
                self.session.add(ticket)
                await self.session.flush()
                self.session.add_all(
                    [
                        TicketMessage(
                            organization_id=org,
                            ticket_id=ticket.id,
                            author_type=MessageAuthorType.CUSTOMER,
                            body=row.message,
                        ),
                        TicketMessage(
                            organization_id=org,
                            ticket_id=ticket.id,
                            author_id=agent.id,
                            author_type=MessageAuthorType.AGENT,
                            body=row.reply,
                        ),
                    ]
                )
                await self.session.flush()
                self.ticket_ids[row.id] = ticket.id
            await self.session.commit()

    async def ask(self, question: str, *, probe: bool) -> tuple[AIRun, list[Any]]:
        retrieval = RetrievalService(
            self.session, embedder=self.embedder, vector_store=self.vectors, reranker=self.reranker
        )
        run, results = await AnswerService(
            self.session, retrieval=retrieval, llm=self.llm, nli=self.nli
        ).answer_question(self.workspace.member, self.workspace.user, AskRequest(query=question))
        if probe:
            run.config = {**(run.config or {}), "origin": PROBE_ORIGIN}
            await self.session.commit()
        return run, results

    @staticmethod
    def _answer(run: AIRun) -> dict[str, Any]:
        answer = run.answer
        return {
            "run_id": str(run.id),
            "status": answer.status.value if answer else None,
            "text": answer.text if answer else None,
            "documents": sorted(
                {str(c.document_id) for c in (answer.citations if answer else []) if c.document_id}
            ),
            "latency_ms": round(run.latency_ms),
        }

    async def ask_all(
        self, questions: Sequence[tuple[str, str]], *, probe: bool, label: str
    ) -> list[dict[str, Any]]:
        """(topic id, question) pairs through the pipeline."""
        out = []
        for n, (topic_id, question) in enumerate(questions, start=1):
            run, _ = await self.ask(question, probe=probe)
            record = {"topic": topic_id, "question": question, **self._answer(run)}
            out.append(record)
            self.progress(f"{label} {n}/{len(questions)} {record['status']}: {question[:60]}")
        return out

    async def regression(self, questions: Sequence[Question], label: str) -> list[QuestionOutcome]:
        outcomes = []
        for n, question in enumerate(questions, start=1):
            run, results = await self.ask(question.question, probe=True)
            outcome = outcome_of(question, run, results, top_k=5)
            outcomes.append(outcome)
            self.progress(f"{label} {n}/{len(questions)} {outcome.status}: {question.id}")
        return outcomes

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await to_thread.run_sync(self.embedder.embed_documents, texts)

    async def gaps(self) -> list[tuple[KnowledgeGap, list[KnowledgeGapQuestion]]]:
        gaps = (
            await self.session.scalars(
                select(KnowledgeGap)
                .where(KnowledgeGap.organization_id == self.organization_id)
                .order_by(KnowledgeGap.asked.desc(), KnowledgeGap.label)
            )
        ).all()
        questions = (
            await self.session.scalars(
                select(KnowledgeGapQuestion).where(
                    KnowledgeGapQuestion.organization_id == self.organization_id
                )
            )
        ).all()
        return [(g, [q for q in questions if q.gap_id == g.id]) for g in gaps]

    async def publish(self, gap: KnowledgeGap) -> uuid.UUID | None:
        """Publish the draft as it is (internal) and index it in-process."""
        row = await publish_gap(
            self.session,
            self.workspace.member,
            self.workspace.user,
            gap.id,
            title=gap.draft_title or gap.label,
            body=gap.draft_body or "",
            visibility=DocumentVisibility.INTERNAL,
            storage=self.storage,
            queue=_NoQueue(),
        )
        document = row.document
        if document is None:
            return None
        outcome = await IngestionService(
            self.session, self.storage, self.embedder, self.vectors, self.limits
        ).run(document.id, document.ingestion_id)
        if outcome is not Outcome.INDEXED:
            logger.warning("Couldn't index the article for gap %s: %s", gap.id, outcome)
            return None
        return document.id


async def run_experiment(
    run: GapRun,
    topics: Sequence[Topic],
    tickets: Sequence[TicketRow],
    *,
    cluster_similarity: float | None = None,
    regression: Sequence[Question] = (),
) -> dict[str, Any]:
    by_question = {q: t.id for t in topics for q in t.asked}
    topic_of = {t.id: t for t in topics}
    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat()}

    await run.reset()
    await run.seed_tickets(tickets)
    run.progress(f"Seeded {len(tickets)} resolved tickets")

    # --- G0: which asked questions the documents can't answer ------------------------------
    asked = await run.ask_all(
        [(t.id, q) for t in topics for q in t.asked], probe=False, label="asked"
    )
    held_before = await run.ask_all(
        [(t.id, h.question) for t in topics for h in t.held_out],
        probe=True,
        label="held-out before",
    )
    regression_before = await run.regression(regression, "regression before") if regression else []
    gap_questions = [a for a in asked if a["status"] in GAP_STATUS_VALUES]
    report["g0"] = {
        "asked": len(asked),
        "unanswered": len(gap_questions),
        "by_status": dict(Counter(a["status"] for a in asked)),
        "answered_questions": [
            {
                "topic": a["topic"],
                "question": a["question"],
                "status": a["status"],
                "text": a["text"],
            }
            for a in asked
            if a["status"] not in GAP_STATUS_VALUES
        ],
    }

    # --- G1: grouping ----------------------------------------------------------------------
    vectors = await run.embed([a["question"] for a in gap_questions])
    g1: dict[str, Any] = {}
    for split in ("dev", "test", "all"):
        idx = [
            i
            for i, a in enumerate(gap_questions)
            if split == "all" or topic_of[a["topic"]].split == split
        ]
        g1[split] = sweep(
            [gap_questions[i]["question"] for i in idx],
            [gap_questions[i]["topic"] for i in idx],
            [vectors[i] for i in idx],
        )
    chosen = choose_threshold(g1["dev"])
    used = cluster_similarity if cluster_similarity is not None else chosen
    analysis = await GapAnalyzer(
        run.session, run.embedder, join_similarity=used, cluster_similarity=used
    ).analyze(run.organization_id)
    gaps = await run.gaps()
    live_labels_true, live_labels_pred = [], []
    gap_topic: dict[uuid.UUID, str] = {}
    for g_index, (gap, questions) in enumerate(gaps):
        members = [by_question.get(q.query, "?") for q in questions]
        gap_topic[gap.id] = Counter(members).most_common(1)[0][0] if members else "?"
        for member in members:
            live_labels_true.append(member)
            live_labels_pred.append(g_index)
    report["g1"] = {
        "sweep": g1,
        "chosen_on_dev": chosen,
        "used": used,
        "live": {
            **pairwise(live_labels_true, live_labels_pred),
            "analysis": analysis.to_json(),
            "gaps": [
                {
                    "label": gap.label,
                    "topic": gap_topic[gap.id],
                    "questions": [q.query for q in questions],
                    "topics": [by_question.get(q.query, "?") for q in questions],
                }
                for gap, questions in gaps
            ],
        },
    }
    run.progress(f"G1: {len(gaps)} gaps at threshold {used} (dev choice {chosen})")

    # --- G2: drafting ----------------------------------------------------------------------
    drafter = GapDrafter(run.session, run.llm, run.embedder, run.vectors, nli=run.nli)
    dataset_id = {v: k for k, v in run.ticket_ids.items()}
    private = drafting.private_terms(
        [t.customer for t in tickets] + [run.workspace.user.full_name],
        [t.email for t in tickets],
    )
    drafts = []
    for n, (gap, _) in enumerate(gaps, start=1):
        outcome = await drafter.draft(gap)
        await drafter.record(gap.id, outcome)
        topic = topic_of.get(gap_topic[gap.id])
        draft = outcome.draft
        body = draft.body() if draft else ""
        sources = [dataset_id.get(s.ticket_id, "?") for s in outcome.sources]
        gold = topic.sources if topic else []
        statements = draft.statements if draft else ()
        drafts.append(
            {
                "gap": gap.label,
                "topic": gap_topic[gap.id],
                "answerable": bool(topic and topic.answerable),
                "status": outcome.status.value,
                "verdict": draft.verdict if draft else None,
                "title": draft.title if draft else None,
                "body": body,
                "sources": sources,
                "gold_sources": gold,
                "source_hits": sum(s in gold for s in sources),
                "facts": facts_found(body, topic.draft_facts) if topic else [],
                "outdated_in_body": [
                    p for p in (topic.outdated if topic else []) if normalize(p) in normalize(body)
                ],
                "outdated_in_statements": [
                    p
                    for p in (topic.outdated if topic else [])
                    if any(normalize(p) in normalize(s.text) for s in statements)
                ],
                "private_in_body": drafting.find_private(body, private),
                "statements": [s.to_json() for s in statements],
                "conflicts": [c.to_json() for c in outcome.conflicts],
                "conflict_checks": outcome.checks,
                "error": outcome.error,
                "latency_ms": outcome.latency_ms,
            }
        )
        run.progress(f"G2 draft {n}/{len(gaps)} {outcome.status.value}: {gap.label[:60]}")
    report["g2"] = {"drafts": drafts, "summary": g2_summary(drafts, topics)}

    # --- G3: effect ------------------------------------------------------------------------
    published = []
    for gap, _ in gaps:
        fresh = await run.session.get(KnowledgeGap, gap.id, populate_existing=True)
        if (
            fresh is None
            or fresh.draft_status != DraftStatus.DRAFTED
            or fresh.status != GapStatus.OPEN
        ):
            continue
        document_id = await run.publish(fresh)
        if document_id is not None:
            published.append((fresh, document_id))
            run.progress(f"G3 published: {fresh.draft_title or fresh.label}")
    rechecks = []
    for gap, _document_id in published:
        fresh = await run.session.get(KnowledgeGap, gap.id, populate_existing=True)
        assert fresh is not None
        summary = await recheck_gap(
            run.session,
            fresh,
            run.workspace.member,
            run.workspace.user,
            llm=run.llm,
            embedder=run.embedder,
            vectors=run.vectors,
            reranker=run.reranker,
            nli=run.nli,
        )
        rechecks.append({"gap": fresh.label, "topic": gap_topic[gap.id], **summary})
    held_after = await run.ask_all(
        [(t.id, h.question) for t in topics for h in t.held_out], probe=True, label="held-out after"
    )
    regression_after = await run.regression(regression, "regression after") if regression else []
    articles = {str(d) for _, d in published}
    report["g3"] = g3_summary(topics, held_before, held_after, rechecks, articles)
    if regression:
        report["regression"] = regression_summary(regression_before, regression_after)
    report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def g2_summary(drafts: Sequence[dict[str, Any]], topics: Sequence[Topic]) -> dict[str, Any]:
    answerable = {t.id for t in topics if t.answerable}
    decoys = {t.id for t in topics if not t.answerable}
    # One gap per topic is expected; the topic's largest gap stands for it.
    seen: set[str] = set()
    main = []
    for d in drafts:
        if d["topic"] not in seen:
            seen.add(d["topic"])
            main.append(d)
    ans = [d for d in main if d["topic"] in answerable]
    dec = [d for d in main if d["topic"] in decoys]
    statements = [s for d in drafts for s in d["statements"]]
    problems = Counter(p for s in statements for p in s["problems"])
    facts = [f for d in ans for f in d["facts"]]
    gold = sum(len(d["gold_sources"]) for d in ans)
    selected = sum(len(d["sources"]) for d in ans)
    return {
        "gaps": len(drafts),
        "topics_with_a_gap": len(main),
        "answerable_drafted": rate(sum(d["status"] == "drafted" for d in ans), len(ans)),
        "decoys_drafted": rate(sum(d["status"] == "drafted" for d in dec), len(dec)),
        "decoy_statuses": {d["topic"]: d["status"] for d in dec},
        "source_recall": rate(sum(d["source_hits"] for d in ans), gold),
        "source_precision": rate(sum(d["source_hits"] for d in ans), selected),
        "key_facts_in_body": rate(sum(facts), len(facts)),
        "drafts_with_every_fact": rate(
            sum(bool(d["facts"]) and all(d["facts"]) for d in ans), len(ans)
        ),
        "statements": len(statements),
        "included": sum(s["included"] for s in statements),
        "left_out_by": dict(problems),
        "private_details_in_bodies": sum(len(d["private_in_body"]) for d in drafts),
        "outdated_claims_in_bodies": sum(len(d["outdated_in_body"]) for d in drafts),
        "outdated_claims_in_statements": sum(len(d["outdated_in_statements"]) for d in drafts),
        "conflicts_flagged": sum(len(d["conflicts"]) for d in drafts),
        "conflict_checks": sum(len(d["conflict_checks"]) for d in drafts),
        "latency_ms_median": sorted(d["latency_ms"] for d in drafts)[len(drafts) // 2]
        if drafts
        else None,
    }


def g3_summary(
    topics: Sequence[Topic],
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]],
    rechecks: Sequence[dict[str, Any]],
    articles: set[str],
) -> dict[str, Any]:
    facts = {h.question: h.key_facts for t in topics for h in t.held_out}
    answerable = {t.id for t in topics if t.answerable}

    def correct(record: dict[str, Any]) -> bool:
        return record["status"] == "answered" and all(
            facts_found(record["text"], facts[record["question"]])
        )

    rows = []
    for b, a in zip(before, after, strict=True):
        assert b["question"] == a["question"]
        rows.append(
            {
                "topic": b["topic"],
                "question": b["question"],
                "before": b["status"],
                "after": a["status"],
                "correct_before": correct(b),
                "correct_after": correct(a),
                "cites_article": bool(set(a["documents"]) & articles),
                "answer_after": a["text"],
            }
        )
    ans = [r for r in rows if r["topic"] in answerable]
    dec = [r for r in rows if r["topic"] not in answerable]
    b_only = sum(r["correct_before"] and not r["correct_after"] for r in ans)
    a_only = sum(r["correct_after"] and not r["correct_before"] for r in ans)
    return {
        "held_out": rows,
        "answerable_questions": len(ans),
        "answered_before": rate(sum(r["before"] == "answered" for r in ans), len(ans)),
        "answered_after": rate(sum(r["after"] == "answered" for r in ans), len(ans)),
        "correct_before": rate(sum(r["correct_before"] for r in ans), len(ans)),
        "correct_after": rate(sum(r["correct_after"] for r in ans), len(ans)),
        "cite_article_after": rate(sum(r["cites_article"] for r in ans), len(ans)),
        "answered_without_every_fact_after": sum(
            r["after"] == "answered" and not r["correct_after"] for r in ans
        ),
        "mcnemar": {
            "only_before": b_only,
            "only_after": a_only,
            "p": exact_mcnemar(b_only, a_only),
        },
        "decoys_answered_after": sum(r["after"] == "answered" for r in dec),
        "decoy_questions": len(dec),
        "rechecks": list(rechecks),
        "recheck_answered": rate(
            sum(r["answered"] for r in rechecks), sum(r["total"] for r in rechecks)
        ),
    }


def regression_summary(
    before: Sequence[QuestionOutcome], after: Sequence[QuestionOutcome]
) -> dict[str, Any]:
    changed = [
        {
            "id": b.question.id,
            "category": b.question.category,
            "expected": b.question.expected_action,
            "before": b.status,
            "after": a.status,
            "correct_before": b.correct,
            "correct_after": a.correct,
        }
        for b, a in zip(before, after, strict=True)
        if b.status != a.status or b.correct != a.correct
    ]
    keys = ("decision_accuracy", "correctness", "unwarranted_answers", "coverage")
    mb, ma = answer_metrics(before), answer_metrics(after)
    return {
        "questions": len(before),
        "before": {k: mb[k] for k in keys},
        "after": {k: ma[k] for k in keys},
        "changed": changed,
    }


# --- Report --------------------------------------------------------------------------------


def _pct(r: dict[str, Any] | None) -> str:
    if not r or r.get("n", 0) == 0:
        return "n/a"
    ci = r.get("ci95")
    text = f"{r['successes']}/{r['n']} ({r['value'] * 100:.0f}%"
    return text + (f", CI {ci[0] * 100:.0f}-{ci[1] * 100:.0f}%)" if ci else ")")


def markdown(report: dict[str, Any]) -> str:
    g0, g1, g2, g3 = report["g0"], report["g1"], report["g2"]["summary"], report["g3"]
    lines = [
        f"# Knowledge gaps to articles: {report.get('name', 'G1-G3')}",
        "",
        f"Model: {report.get('model')} · embeddings: {report.get('embedding_model')} · "
        f"cache hits {report.get('cache', {}).get('hits')}, model calls "
        f"{report.get('cache', {}).get('misses')}",
        "",
        "## G0: questions the documents can't answer",
        "",
        f"{g0['unanswered']} of {g0['asked']} asked questions went unanswered: "
        f"{json.dumps(g0['by_status'])}.",
        "",
        "## G1: grouping",
        "",
        f"Threshold chosen on dev: **{g1['chosen_on_dev']}**; used in the live analysis: "
        f"**{g1['used']}**.",
        "",
        "| Threshold | Dev pair F1 | Test pair P | Test pair R | Test pair F1 | Test B³ F1 | "
        "Test clusters / topics |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d, t in zip(g1["sweep"]["dev"], g1["sweep"]["test"], strict=True):
        lines.append(
            f"| {d['threshold']} | {d['pairs_f1']} | {t['pairs_precision']} | "
            f"{t['pairs_recall']} | {t['pairs_f1']} | {t['bcubed_f1']} | "
            f"{t['clusters']} / {t['topics']} |"
        )
    live = g1["live"]
    lines += [
        "",
        f"Live analysis (all questions): {len(live['gaps'])} gaps for {live['topics']} topics; "
        f"pair P {live['pairs_precision']}, R {live['pairs_recall']}, F1 {live['pairs_f1']}.",
        "",
        "## G2: drafting",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        f"| Answerable topics drafted | {_pct(g2['answerable_drafted'])} |",
        f"| Decoys drafted (must be 0) | {_pct(g2['decoys_drafted'])} {g2['decoy_statuses']} |",
        f"| Source recall | {_pct(g2['source_recall'])} |",
        f"| Source precision | {_pct(g2['source_precision'])} |",
        f"| Key facts in the body | {_pct(g2['key_facts_in_body'])} |",
        f"| Drafts with every key fact | {_pct(g2['drafts_with_every_fact'])} |",
        f"| Statements kept / written | {g2['included']} / {g2['statements']} |",
        f"| Left out by | {g2['left_out_by']} |",
        f"| Customers' details in a body (must be 0) | {g2['private_details_in_bodies']} |",
        f"| Outdated claim in a body (must be 0) | {g2['outdated_claims_in_bodies']} "
        f"(written in {g2['outdated_claims_in_statements']} statement(s)) |",
        f"| Conflicts flagged / passages checked | {g2['conflicts_flagged']} / "
        f"{g2['conflict_checks']} |",
        f"| Median drafting time | {g2['latency_ms_median']} ms |",
        "",
        "## G3: effect of publishing the drafts unedited",
        "",
        "| Held-out paraphrases (answerable topics) | Before | After |",
        "| --- | --- | --- |",
        f"| Answered | {_pct(g3['answered_before'])} | {_pct(g3['answered_after'])} |",
        f"| Answered with every key fact | {_pct(g3['correct_before'])} | "
        f"{_pct(g3['correct_after'])} |",
        "",
        f"Exact McNemar on correctness: {g3['mcnemar']['only_after']} only after, "
        f"{g3['mcnemar']['only_before']} only before, p = {g3['mcnemar']['p']:.4g}. "
        f"Answers citing an article: {_pct(g3['cite_article_after'])}. Answered after without "
        f"every key fact: {g3['answered_without_every_fact_after']}. Decoy questions answered "
        f"after (must be 0): {g3['decoys_answered_after']} of {g3['decoy_questions']}. "
        f"In-product recheck of the gaps' own questions: {_pct(g3['recheck_answered'])}.",
    ]
    if "regression" in report:
        reg = report["regression"]
        lines += ["", "## Regression: the Quillon test split before and after", ""]
        for key in reg["before"]:
            lines.append(f"- {key}: {_pct(reg['before'][key])} → {_pct(reg['after'][key])}")
        lines.append(f"- Questions whose outcome changed: {len(reg['changed'])}")
    lines += ["", "## Drafts", ""]
    for d in report["g2"]["drafts"]:
        lines += [
            f"### {d['gap']} ({d['topic']}, {d['status']})",
            "",
            f"Sources {d['sources']} (gold {d['gold_sources']}); facts {d['facts']}; "
            f"private in body {d['private_in_body']}; conflicts {len(d['conflicts'])}.",
            "",
            f"> **{d['title'] or ''}** {d['body'] or '(no body)'}",
            "",
        ]
        for s in d["statements"]:
            mark = "kept" if s["included"] else f"left out: {', '.join(s['problems'])}"
            lines.append(f"- [{mark}] {s['text']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def write(report: dict[str, Any], output: Path, name: str) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output / f"{stamp}-{name}.json"
    md_path = output / f"{stamp}-{name}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), "utf-8")
    md_path.write_text(markdown(report), "utf-8")
    return json_path, md_path
