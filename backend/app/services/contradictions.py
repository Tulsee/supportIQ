"""Knowledge-base contradiction scanning (Module 13).

A scan:

1. resolves open findings whose passage was replaced by a re-index (the copy shows
   what conflicted; the new text is checked like any other);
2. pairs every indexed passage with its nearest passages in other documents
   (``ai_engine.contradictions.candidates``);
3. sends the pairs not judged before (new or changed text), most similar first and up
   to a per-scan limit, to the model (``ai_engine.contradictions.judge``), verifies the
   quotes, and records every judgement; flagged pairs are findings for admins.

The model is called outside any transaction and each pair is committed on its own,
so a long scan shows progress and a crash loses at most one pair. Only one scan runs
per organization at a time (a partial unique index on running scans). Scans run in a
worker: on demand by an admin, or on a schedule for organizations that turned
scanning on.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.contradictions import judge
from ai_engine.contradictions.candidates import CandidatePair, Neighbour, Passage, select_pairs
from ai_engine.contradictions.judge import Judgement, PassageText, ScanPolicy
from ai_engine.generation.llm import LLMClient, LLMError, Prompt
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.config import settings
from app.core.database import set_rls_context
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models import (
    ContradictionCheck,
    ContradictionScan,
    Document,
    DocumentChunk,
    FindingStatus,
    Organization,
    OrganizationMember,
    OrgRole,
    ScanStatus,
    User,
)
from app.schemas.organizations import OrganizationSettings
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.workers.queue import ContradictionQueue, QueueUnavailableError

logger = logging.getLogger(__name__)

MAX_PAGE = 100
# A scan still "running" after this long was interrupted (a worker crashed).
STALE_SCAN = timedelta(hours=3)
Trigger = Literal["manual", "scheduled"]


def default_policy() -> ScanPolicy:
    return ScanPolicy(
        require_grounding=settings.contradiction_require_grounding,
        min_confidence=settings.contradiction_min_confidence,
        both_orders=settings.contradiction_both_orders,
    )


@dataclass(frozen=True)
class PassageInfo:
    """A candidate passage with what the prompt and the record need."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section: str | None
    content: str


@dataclass(frozen=True)
class Candidate:
    pair: CandidatePair
    a: PassageInfo
    b: PassageInfo


@dataclass(frozen=True)
class PairResult:
    """The model's judgement(s) of one pair, verified, and the policy's decision."""

    judgements: list[Judgement | None]  # A-B first, then B-A (aligned to A-B) if asked
    raw: list[str | None]
    flagged: bool
    model: str | None
    latency_ms: int
    error: str | None

    @property
    def first(self) -> Judgement | None:
        return self.judgements[0] if self.judgements else None


class ContradictionScanner:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMClient,
        vectors: QdrantVectorStore,
        policy: ScanPolicy | None = None,
        *,
        neighbours: int | None = None,
        min_similarity: float | None = None,
        min_tokens: int | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.vectors = vectors
        self.policy = policy or default_policy()
        self.neighbours = neighbours or settings.contradiction_neighbours
        self.min_similarity = (
            settings.contradiction_min_similarity if min_similarity is None else min_similarity
        )
        self.min_tokens = settings.contradiction_min_tokens if min_tokens is None else min_tokens

    # --- Steps (also used by the evaluation) -----------------------------------------

    async def candidates(self, organization_id: uuid.UUID) -> list[Candidate]:
        """Pairs of similar passages from different documents, most similar first."""
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
                    DocumentChunk.organization_id == organization_id,
                    DocumentChunk.token_count >= self.min_tokens,
                    Document.indexed_version.is_not(None),
                )
                .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            )
        ).all()
        info = {
            chunk_id: PassageInfo(chunk_id, document_id, title, section, content)
            for chunk_id, document_id, section, content, title in rows
        }
        await self.session.commit()  # don't hold a transaction open across Qdrant calls
        neighbours: dict[uuid.UUID, list[Neighbour]] = {}
        for passage in info.values():
            hits = await self.vectors.neighbours(
                organization_id,
                passage.chunk_id,
                passage.document_id,
                # Short passages are skipped after the search: ask for a few more.
                limit=self.neighbours + 5,
                min_similarity=self.min_similarity,
            )
            neighbours[passage.chunk_id] = [Neighbour(h.chunk_id, h.score) for h in hits]
        pairs = select_pairs(
            [Passage(p.chunk_id, p.document_id, p.content) for p in info.values()],
            neighbours,
            per_passage=self.neighbours,
            min_similarity=self.min_similarity,
        )
        return [Candidate(pair, info[pair.a.chunk_id], info[pair.b.chunk_id]) for pair in pairs]

    async def ask(self, first: PassageInfo, second: PassageInfo) -> tuple[Judgement | None, str]:
        completion = await self.llm.complete(
            Prompt(
                judge.SYSTEM,
                judge.prompt_user(
                    PassageText(first.document_title, first.section, first.content),
                    PassageText(second.document_title, second.section, second.content),
                ),
                judge.SCHEMA,
            )
        )
        parsed = judge.parse(completion.text)
        return (
            judge.verify(parsed, first.content, second.content) if parsed else None,
            completion.text,
        )

    async def judge_pair(self, candidate: Candidate) -> PairResult:
        """Ask the model (once, or in both orders under the policy) and decide."""
        started = time.perf_counter()
        judgements: list[Judgement | None] = []
        raw: list[str | None] = []
        error = None
        try:
            judgement, text = await self.ask(candidate.a, candidate.b)
            judgements.append(judgement)
            raw.append(text)
            if self.policy.both_orders and judge.flagged(judgement, self.policy):
                reverse, text = await self.ask(candidate.b, candidate.a)
                judgements.append(judge.swap(reverse))
                raw.append(text)
            if judgements[0] is None:
                error = "The model's reply wasn't valid JSON."
        except LLMError as exc:
            error = str(exc)
        return PairResult(
            judgements=judgements,
            raw=raw,
            flagged=error is None and judge.decide(judgements, self.policy),
            model=getattr(self.llm, "model", None),
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )

    # --- A scan ----------------------------------------------------------------------

    async def scan(self, scan: ContradictionScan, *, max_pairs: int | None = None) -> None:
        """Run ``scan`` (already recorded as running) to the end and record the counts.
        The session's RLS context must allow the scan's organization."""
        organization_id = scan.organization_id
        limit = settings.contradiction_max_pairs_per_scan if max_pairs is None else max_pairs
        try:
            auto_resolved = await self._resolve_replaced(organization_id)
            candidates = await self.candidates(organization_id)
            done = {
                key
                for key, verdict in (
                    await self.session.execute(
                        select(ContradictionCheck.pair_key, ContradictionCheck.verdict).where(
                            ContradictionCheck.organization_id == organization_id,
                            ContradictionCheck.pair_key.in_([c.pair.key for c in candidates]),
                        )
                    )
                ).all()
                if verdict is not None  # failed judgements are tried again
            }
            pending = [c for c in candidates if c.pair.key not in done]
            scan = await self._refresh(scan)
            scan.auto_resolved = auto_resolved
            scan.candidates = len(candidates)
            scan.remaining = max(0, len(pending) - limit)
            await self.session.commit()

            for candidate in pending[:limit]:
                result = await self.judge_pair(candidate)
                await self._record(organization_id, candidate, result)
                scan = await self._refresh(scan)
                scan.checked += 1
                scan.findings += int(result.flagged)
                scan.failed += int(result.error is not None)
                await self.session.commit()
            scan.status = ScanStatus.FINISHED
        except Exception as exc:
            logger.exception("Contradiction scan %s failed", scan.id)
            await self.session.rollback()
            scan = await self._refresh(scan)
            scan.status = ScanStatus.FAILED
            scan.error = f"{type(exc).__name__}: {exc}"[:500]
        scan.finished_at = datetime.now(UTC)
        await self.session.commit()

    async def _refresh(self, scan: ContradictionScan) -> ContradictionScan:
        fresh = await self.session.get(ContradictionScan, scan.id, populate_existing=True)
        if fresh is None:  # the organization was deleted mid-scan
            raise NotFoundError("Scan not found")
        return fresh

    async def _resolve_replaced(self, organization_id: uuid.UUID) -> int:
        """Open findings whose passage no longer exists (its document was re-indexed or
        a version uploaded): resolved, as the text they quote is gone."""
        result = await self.session.execute(
            update(ContradictionCheck)
            .where(
                ContradictionCheck.organization_id == organization_id,
                ContradictionCheck.status == FindingStatus.OPEN,
                (ContradictionCheck.chunk_a_id.is_(None))
                | (ContradictionCheck.chunk_b_id.is_(None)),
            )
            .values(
                status=FindingStatus.RESOLVED,
                status_reason="document_changed",
                reviewed_at=func.now(),
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def _record(
        self, organization_id: uuid.UUID, candidate: Candidate, result: PairResult
    ) -> None:
        first = result.first
        values: dict[str, Any] = {
            "document_a_id": candidate.a.document_id,
            "document_b_id": candidate.b.document_id,
            "chunk_a_id": candidate.a.chunk_id,
            "chunk_b_id": candidate.b.chunk_id,
            "section_a": candidate.a.section,
            "section_b": candidate.b.section,
            "passage_a": candidate.a.content,
            "passage_b": candidate.b.content,
            "similarity": round(candidate.pair.similarity, 4),
            "verdict": first.verdict if first and result.error is None else None,
            "confidence": first.confidence if first else 0.0,
            "flagged": result.flagged,
            "conflicts": [c.to_json() for c in first.conflicts] if first else [],
            "signals": {
                "judgements": [j.to_json() if j else None for j in result.judgements],
                "raw": [r[:4000] if r else None for r in result.raw],
                "policy": {
                    "require_grounding": self.policy.require_grounding,
                    "min_confidence": self.policy.min_confidence,
                    "both_orders": self.policy.both_orders,
                },
            },
            "model": result.model,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "status": FindingStatus.OPEN if result.flagged else None,
        }
        existing = await self.session.scalar(
            select(ContradictionCheck)
            .where(
                ContradictionCheck.organization_id == organization_id,
                ContradictionCheck.pair_key == candidate.pair.key,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.verdict is not None:  # judged meanwhile by another scan
                return
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            self.session.add(
                ContradictionCheck(
                    organization_id=organization_id, pair_key=candidate.pair.key, **values
                )
            )
        try:
            await self.session.flush()
        except IntegrityError:  # recorded concurrently, or a document was just deleted
            await self.session.rollback()


# --- Starting scans ---------------------------------------------------------------------


async def _close_stale_scans(session: AsyncSession, organization_id: uuid.UUID) -> None:
    await session.execute(
        update(ContradictionScan)
        .where(
            ContradictionScan.organization_id == organization_id,
            ContradictionScan.status == ScanStatus.RUNNING,
            ContradictionScan.started_at < datetime.now(UTC) - STALE_SCAN,
        )
        .values(status=ScanStatus.FAILED, error="Interrupted", finished_at=func.now())
    )


async def start_scan(
    session: AsyncSession,
    organization_id: uuid.UUID,
    trigger: Trigger,
    requested_by_id: uuid.UUID | None = None,
) -> ContradictionScan | None:
    """Record a running scan, or None if one is already running."""
    await _close_stale_scans(session, organization_id)
    scan = ContradictionScan(
        organization_id=organization_id,
        trigger=trigger,
        requested_by_id=requested_by_id,
        status=ScanStatus.RUNNING,
    )
    session.add(scan)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return scan


async def request_scan(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    queue: ContradictionQueue,
) -> ContradictionScan:
    """An admin asks for a scan now. 409 ``scan_running`` if one is running."""
    require_role(member, OrgRole.ADMIN)
    scan = await start_scan(session, member.organization_id, "manual", user.id)
    if scan is None:
        raise ConflictError("A scan is already running.", code="scan_running")
    try:
        await queue.enqueue(scan.id)
    except QueueUnavailableError as exc:
        logger.warning("Couldn't queue contradiction scan %s: %s", scan.id, exc)
        scan.status = ScanStatus.FAILED
        scan.error = "The job queue is unavailable."
        scan.finished_at = datetime.now(UTC)
        await session.commit()
        raise ServiceUnavailableError(
            "The scan couldn't be started because the job queue is unavailable. Try again."
        ) from exc
    return scan


async def schedule_due_scans(session: AsyncSession, queue: ContradictionQueue) -> int:
    """For each organization with scanning on, start a scan and queue it when the last
    one is older than the interval, or finished with pairs left over (the per-scan
    limit): a first pass over a large knowledge base continues at the next check
    instead of waiting hours. Safe to run twice at once (one running scan per
    organization)."""
    interval = timedelta(minutes=settings.contradiction_scan_interval_minutes)
    await set_rls_context(session, bypass=True)
    organizations = (await session.execute(select(Organization.id, Organization.settings))).all()
    started = 0
    for organization_id, organization_settings in organizations:
        if (
            OrganizationSettings.model_validate(organization_settings or {}).contradiction_scan
            != "on"
        ):
            continue
        last = (
            await session.execute(
                select(
                    ContradictionScan.started_at,
                    ContradictionScan.status,
                    ContradictionScan.remaining,
                )
                .where(ContradictionScan.organization_id == organization_id)
                .order_by(
                    ContradictionScan.started_at.desc(),
                    ContradictionScan.finished_at.desc().nulls_first(),
                )
                .limit(1)
            )
        ).first()
        if last is not None:
            started_at, status, remaining = last
            unfinished = status == ScanStatus.FINISHED and remaining > 0
            if not unfinished and started_at > datetime.now(UTC) - interval:
                continue
        scan = await start_scan(session, organization_id, "scheduled")
        if scan is None:
            continue
        try:
            await queue.enqueue(scan.id)
            started += 1
        except QueueUnavailableError:
            logger.exception("Couldn't queue contradiction scan %s", scan.id)
            scan.status = ScanStatus.FAILED
            scan.error = "The job queue is unavailable."
            scan.finished_at = datetime.now(UTC)
            await session.commit()
    return started


async def run_scan(
    session: AsyncSession,
    scan_id: uuid.UUID,
    *,
    llm: LLMClient,
    vectors: QdrantVectorStore,
) -> ContradictionScan | None:
    """The job: run a recorded scan (as a system operation)."""
    await set_rls_context(session, bypass=True)
    scan = await session.get(ContradictionScan, scan_id)
    if scan is None or scan.status != ScanStatus.RUNNING:
        return None
    await ContradictionScanner(session, llm, vectors).scan(scan)
    return scan


async def run_scan_job(scan_id: uuid.UUID) -> str | None:
    from app.core.knowledge import get_embedder, new_qdrant_client, vector_store_for
    from app.core.llm import get_llm_client

    async def job(session: AsyncSession, *_: object) -> str | None:
        client = new_qdrant_client()
        try:
            scan = await run_scan(
                session,
                scan_id,
                llm=get_llm_client(),
                vectors=vector_store_for(client, get_embedder()),
            )
        finally:
            await client.close()
        return scan.status.value if scan else None

    from app.workers.context import with_worker_context

    return await with_worker_context(job)


async def run_schedule_job() -> int:
    from app.workers.context import with_worker_context
    from app.workers.queue import CeleryContradictionQueue

    async def job(session: AsyncSession, *_: object) -> int:
        return await schedule_due_scans(session, CeleryContradictionQueue())

    return await with_worker_context(job)


# --- Reading and reviewing --------------------------------------------------------------


@dataclass(frozen=True)
class FindingRow:
    check: ContradictionCheck
    document_a_title: str
    document_b_title: str
    reviewer: User | None


async def _finding_rows(
    session: AsyncSession, checks: Sequence[ContradictionCheck]
) -> list[FindingRow]:
    document_ids = {c.document_a_id for c in checks} | {c.document_b_id for c in checks}
    titles = (
        dict(
            (
                await session.execute(
                    select(Document.id, Document.title).where(Document.id.in_(document_ids))
                )
            ).all()
        )
        if document_ids
        else {}
    )
    reviewer_ids = {c.reviewed_by_id for c in checks if c.reviewed_by_id}
    reviewers = (
        {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(reviewer_ids))))}
        if reviewer_ids
        else {}
    )
    return [
        FindingRow(
            c,
            titles.get(c.document_a_id, ""),
            titles.get(c.document_b_id, ""),
            reviewers.get(c.reviewed_by_id) if c.reviewed_by_id else None,
        )
        for c in checks
    ]


async def list_findings(
    session: AsyncSession,
    member: OrganizationMember,
    *,
    status: FindingStatus | None,
    document_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[FindingRow], int]:
    require_role(member, *STAFF_ROLES)
    query = select(ContradictionCheck).where(
        ContradictionCheck.organization_id == member.organization_id,
        ContradictionCheck.status.is_not(None),
    )
    if status is not None:
        query = query.where(ContradictionCheck.status == status)
    if document_id is not None:
        query = query.where(
            (ContradictionCheck.document_a_id == document_id)
            | (ContradictionCheck.document_b_id == document_id)
        )
    checks = (
        await session.scalars(
            query.order_by(ContradictionCheck.created_at.desc(), ContradictionCheck.id)
            .limit(min(limit, MAX_PAGE))
            .offset(offset)
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    return await _finding_rows(session, checks), int(total or 0)


async def get_finding(
    session: AsyncSession, member: OrganizationMember, finding_id: uuid.UUID
) -> FindingRow:
    require_role(member, *STAFF_ROLES)
    check = await session.scalar(
        select(ContradictionCheck).where(
            ContradictionCheck.id == finding_id,
            ContradictionCheck.organization_id == member.organization_id,
            ContradictionCheck.status.is_not(None),
        )
    )
    if check is None:
        raise NotFoundError("Finding not found")
    return (await _finding_rows(session, [check]))[0]


async def review_finding(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    finding_id: uuid.UUID,
    status: FindingStatus,
    note: str | None,
) -> FindingRow:
    """An admin dismisses, resolves or reopens a finding (recorded in the audit log)."""
    require_role(member, OrgRole.ADMIN)
    check = await session.scalar(
        select(ContradictionCheck)
        .where(
            ContradictionCheck.id == finding_id,
            ContradictionCheck.organization_id == member.organization_id,
            ContradictionCheck.status.is_not(None),
        )
        .with_for_update()
    )
    if check is None:
        raise NotFoundError("Finding not found")
    before = check.status
    if before != status:
        check.status = status
        check.status_reason = None
        check.reviewed_by_id = user.id
        check.reviewed_at = datetime.now(UTC)
        check.note = (note or "").strip()[:1000] or None
        topic = next((c.get("topic") for c in check.conflicts if c.get("topic")), None)
        await audit.record(
            session,
            organization_id=member.organization_id,
            actor_id=user.id,
            entity_type="contradiction",
            entity_id=check.id,
            action=f"contradiction.{status.value}"
            if status != FindingStatus.OPEN
            else "contradiction.reopened",
            summary=f"Contradiction {'reopened' if status == FindingStatus.OPEN else status.value}"
            + (f": {topic}" if topic else ""),
            changes={"status": {"from": before.value if before else None, "to": status.value}},
        )
        await session.commit()
    return (await _finding_rows(session, [check]))[0]


@dataclass(frozen=True)
class ScanSummary:
    open: int
    dismissed: int
    resolved: int
    pairs_checked: int
    latest: ContradictionScan | None
    scanning_enabled: bool


async def summary(session: AsyncSession, member: OrganizationMember) -> ScanSummary:
    require_role(member, *STAFF_ROLES)
    counts = dict(
        (
            await session.execute(
                select(ContradictionCheck.status, func.count())
                .where(
                    ContradictionCheck.organization_id == member.organization_id,
                    ContradictionCheck.status.is_not(None),
                )
                .group_by(ContradictionCheck.status)
            )
        ).all()
    )
    checked = await session.scalar(
        select(func.count()).where(
            ContradictionCheck.organization_id == member.organization_id,
            ContradictionCheck.verdict.is_not(None),
        )
    )
    latest = await session.scalar(
        select(ContradictionScan)
        .where(ContradictionScan.organization_id == member.organization_id)
        # A running scan first among scans started together.
        .order_by(
            ContradictionScan.started_at.desc(),
            ContradictionScan.finished_at.desc().nulls_first(),
        )
        .limit(1)
    )
    enabled = (
        OrganizationSettings.model_validate(member.organization.settings or {}).contradiction_scan
        == "on"
    )
    return ScanSummary(
        open=counts.get(FindingStatus.OPEN, 0),
        dismissed=counts.get(FindingStatus.DISMISSED, 0),
        resolved=counts.get(FindingStatus.RESOLVED, 0),
        pairs_checked=int(checked or 0),
        latest=latest,
        scanning_enabled=enabled,
    )
