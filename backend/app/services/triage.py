"""AI ticket triage (Module 11).

When a ticket is opened without a category, a job asks two signals what it's about:

1. The language model, with JSON output constrained to the organization's category
   keys (or "unsure"), plus a suggested priority, the language and urgency cues.
2. A vote of similar past tickets whose category a person set or confirmed.

``ai_engine.triage.combine`` merges them; at or above the threshold the prediction is
``confident``. In the organization's triage mode ``on``, a confident category is
applied (an automatic change in the timeline) and the language is stored; otherwise
the top guess is only a suggestion staff can accept. In ``shadow`` mode nothing is
shown or changed: predictions are recorded to measure accuracy first. Priority is
only ever suggested.

Routing runs right after triage in the same job, so rules see the category.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from anyio import to_thread
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import Embedder
from ai_engine.generation.llm import LLMClient, LLMError, Prompt
from ai_engine.triage import llm as triage_llm
from ai_engine.triage.combine import TriagePolicy, combine
from ai_engine.triage.neighbours import vote
from app.core.config import settings
from app.core.database import set_rls_context
from app.core.exceptions import ConflictError, NotFoundError
from app.models import (
    Organization,
    OrganizationMember,
    Ticket,
    TicketCategory,
    TicketEvent,
    TicketEventType,
    TicketMessage,
    TicketStatus,
    TicketTriage,
    TriageDecisionKind,
    TriageLabel,
    User,
)
from app.repositories.tickets import TicketRepository
from app.schemas.organizations import OrganizationSettings, TriageMode
from app.services.authorization import STAFF_ROLES, require_role
from app.services.notifications import NotificationService
from app.services.routing import enqueue_new_ticket, route_new_ticket
from app.services.tickets import TicketService
from app.services.triage_labels import record_category_choice
from app.websocket.broker import EventBroker
from app.websocket.presence import PresenceTracker
from app.workers.context import with_worker_context
from app.workers.queue import NotificationMailer, QueueUnavailableError, RoutingQueue, TriageQueue

logger = logging.getLogger(__name__)

MAX_TEXT = 2000
# Labels embedded per triage run (older ones catch up over the next runs).
EMBED_BATCH = 200


def triage_mode(organization_settings: dict[str, Any] | None) -> TriageMode:
    return OrganizationSettings.model_validate(organization_settings or {}).triage_mode


def default_policy() -> TriagePolicy:
    return TriagePolicy(alpha=settings.triage_alpha, threshold=settings.triage_threshold)


def _text(subject: str, body: str) -> str:
    return f"{subject.strip()}\n\n{body.strip()}"[:MAX_TEXT]


async def _opening_messages(
    session: AsyncSession, ticket_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    """Subject and opening message of each ticket."""
    first = (
        select(TicketMessage.ticket_id, func.min(TicketMessage.seq).label("seq"))
        .where(TicketMessage.ticket_id.in_(ticket_ids))
        .group_by(TicketMessage.ticket_id)
        .subquery()
    )
    rows = await session.execute(
        select(Ticket.id, Ticket.subject, TicketMessage.body)
        .join(first, first.c.ticket_id == Ticket.id)
        .join(
            TicketMessage,
            (TicketMessage.ticket_id == Ticket.id) & (TicketMessage.seq == first.c.seq),
        )
    )
    return {ticket_id: (subject, body) for ticket_id, subject, body in rows.all()}


@dataclass(frozen=True)
class _Category:
    id: uuid.UUID
    key: str
    name: str
    description: str


class TriageService:
    def __init__(
        self,
        session: AsyncSession,
        llm: LLMClient,
        embedder: Embedder,
        events: EventBroker | None = None,
        policy: TriagePolicy | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.embedder = embedder
        self.events = events
        self.policy = policy or default_policy()
        self.tickets = TicketRepository(session)

    async def triage(
        self, organization_id: uuid.UUID, ticket_id: uuid.UUID, mode: TriageMode
    ) -> TicketTriage | None:
        """Predict (and in mode ``on``, apply) and commit. None when there's nothing to
        do: a person chose the category (before or while triage ran), the ticket is
        resolved or closed, it was triaged before, or the organization has fewer than
        two categories.

        Nothing is locked while the model thinks (tens of seconds on a laptop): the
        inputs are read first, the model is called outside any transaction, and only
        then is the ticket locked, checked again and updated."""
        started = time.perf_counter()
        ticket = await self.tickets.get(organization_id, ticket_id)
        if not await self._still_open(ticket):
            return None
        categories = [
            _Category(c.id, c.key, c.name, c.description)
            for c in (
                await self.session.scalars(
                    select(TicketCategory)
                    .where(
                        TicketCategory.organization_id == organization_id,
                        TicketCategory.is_active.is_(True),
                    )
                    .order_by(TicketCategory.name)
                )
            ).all()
        ]
        if len(categories) < 2:
            return None
        by_key = {c.key: c for c in categories}
        subject, body = (await _opening_messages(self.session, [ticket_id])).get(
            ticket_id,
            (ticket.subject, ""),  # type: ignore[union-attr]
        )

        # Similar tickets a person labelled (the vote needs no model).
        await self.embed_labels(organization_id)
        vector = (await self._embed([_text(subject, body)]))[0]
        labels = (
            await self.session.execute(
                select(TriageLabel.ticket_id, TriageLabel.category_key, TriageLabel.embedding)
                .where(
                    TriageLabel.organization_id == organization_id,
                    TriageLabel.ticket_id != ticket_id,
                    TriageLabel.embedding_model == self.embedder.model_name,
                    TriageLabel.category_key.in_(list(by_key)),
                )
                .order_by(TriageLabel.updated_at.desc())
                .limit(settings.triage_max_labels)
            )
        ).all()
        neighbours = vote(
            vector,
            [embedding for _, _, embedding in labels],
            [key for _, key, _ in labels],
            k=settings.triage_neighbours,
            min_similarity=settings.triage_min_similarity,
        )
        examples = []
        if neighbours:
            ids = [labels[n.index][0] for n in neighbours.neighbours]
            numbers = dict(
                (
                    await self.session.execute(
                        select(Ticket.id, Ticket.number).where(Ticket.id.in_(ids))
                    )
                ).all()
            )
            examples = [
                {
                    "ticket_id": str(labels[n.index][0]),
                    "number": numbers.get(labels[n.index][0]),
                    "category": n.category,
                    "similarity": round(n.similarity, 4),
                }
                for n in neighbours.neighbours
            ]
        await self.session.commit()  # saves new label embeddings; nothing held from here
        self.session.expire_all()  # re-read the ticket for real after the model call

        # The language model, outside any transaction.
        options = [triage_llm.CategoryOption(c.key, c.name, c.description) for c in categories]
        llm_vote, model, error = None, None, None
        try:
            completion = await self.llm.complete(
                Prompt(
                    system=triage_llm.SYSTEM,
                    user=triage_llm.prompt_user(options, subject, body),
                    json_schema=triage_llm.schema(options),
                )
            )
            model = completion.model
            llm_vote = triage_llm.parse(completion.text, list(by_key))
            if llm_vote is None:
                error = "The model's reply wasn't the expected JSON"
        except LLMError as exc:
            error = str(exc)

        languages = settings.triage_neighbour_languages
        skipped = bool(
            neighbours
            and llm_vote
            and llm_vote.language
            and languages
            and llm_vote.language not in languages
        )
        if skipped:  # the embedder can't read this language: its neighbours are noise
            neighbours, examples = None, []
        decision = combine(llm_vote, neighbours, self.policy)
        if decision.confident:
            kind = TriageDecisionKind.CONFIDENT
        elif llm_vote is None:
            kind = TriageDecisionKind.FAILED
        else:
            kind = TriageDecisionKind.UNSURE
        category = by_key.get(decision.category or "")

        # Now lock, and check nobody chose a category (or triaged it) meanwhile.
        ticket = await self.tickets.get(organization_id, ticket_id, for_update=True)
        if not await self._still_open(ticket):
            await self.session.rollback()
            return None
        assert ticket is not None
        applied = kind == TriageDecisionKind.CONFIDENT and mode == "on" and category is not None
        row = TicketTriage(
            id=uuid.uuid4(),
            organization_id=organization_id,
            ticket_id=ticket.id,
            mode=mode,
            decision=kind,
            applied=applied,
            category_id=category.id if category else None,
            category_key=category.key if category else None,
            category_name=category.name if category else None,
            confidence=decision.confidence,
            threshold=self.policy.threshold,
            alpha=self.policy.alpha,
            agree=decision.agree,
            priority=llm_vote.priority if llm_vote else None,
            language=llm_vote.language if llm_vote else None,
            urgency_cues=list(llm_vote.urgency_cues) if llm_vote else [],
            signals={
                "llm": llm_vote.to_json() if llm_vote else None,
                "neighbours": {"shares": neighbours.shares, "examples": examples}
                if neighbours
                else None,
                "scores": {key: round(score, 4) for key, score in decision.scores.items()},
                "neighbours_skipped": "language" if skipped else None,
            },
            model=model,
            embedding_model=self.embedder.model_name,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=error,
        )
        self.session.add(row)
        tickets = TicketService(self.session, self.events)
        if mode == "on":
            if applied and category is not None:
                await tickets.apply_triage(
                    ticket,
                    category.id,
                    {"source": "triage", "confidence": decision.confidence},
                )
            if llm_vote and llm_vote.language:
                ticket.language = llm_vote.language
        await self.session.commit()
        if mode == "on":
            await tickets.publish_change(ticket, customer_visible=False)
        logger.info("Triaged ticket %s (%s): %s %s", ticket.id, mode, kind, row.category_key)
        return row

    async def _still_open(self, ticket: Ticket | None) -> bool:
        """Triage applies: no category chosen, not resolved or closed, not triaged yet."""
        if (
            ticket is None
            or ticket.category_id is not None
            or ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED)
        ):
            return False
        triaged = await self.session.scalar(
            select(TicketTriage.id).where(TicketTriage.ticket_id == ticket.id)
        )
        return triaged is None

    async def embed_labels(self, organization_id: uuid.UUID, limit: int = EMBED_BATCH) -> int:
        """Embed labels that have no embedding (or one from another model). Doesn't commit."""
        pending = list(
            (
                await self.session.scalars(
                    select(TriageLabel)
                    .where(
                        TriageLabel.organization_id == organization_id,
                        (TriageLabel.embedding.is_(None))
                        | (TriageLabel.embedding_model != self.embedder.model_name),
                    )
                    .limit(limit)
                )
            ).all()
        )
        if not pending:
            return 0
        texts = await _opening_messages(self.session, [label.ticket_id for label in pending])
        ready = [label for label in pending if label.ticket_id in texts]
        vectors = await self._embed([_text(*texts[label.ticket_id]) for label in ready])
        for label, vector in zip(ready, vectors, strict=True):
            label.embedding = [float(x) for x in vector]
            label.embedding_model = self.embedder.model_name
        return len(ready)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        # Symmetric similarity between tickets: both sides embedded the same way.
        return await to_thread.run_sync(self.embedder.embed_documents, texts)


async def backfill_labels(session: AsyncSession) -> int:
    """Make labels of tickets whose current category a person chose before triage
    existed (the latest category change has an actor). Commits; returns how many."""
    await set_rls_context(session, bypass=True)
    latest = (
        select(
            TicketEvent.ticket_id,
            TicketEvent.actor_id,
            func.row_number()
            .over(partition_by=TicketEvent.ticket_id, order_by=TicketEvent.seq.desc())
            .label("n"),
        )
        .where(TicketEvent.type == TicketEventType.CATEGORY_CHANGED)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Ticket.id, Ticket.organization_id, TicketCategory.key)
            .join(TicketCategory, TicketCategory.id == Ticket.category_id)
            .join(latest, (latest.c.ticket_id == Ticket.id) & (latest.c.n == 1))
            .outerjoin(TriageLabel, TriageLabel.ticket_id == Ticket.id)
            .where(latest.c.actor_id.is_not(None), TriageLabel.ticket_id.is_(None))
        )
    ).all()
    for ticket_id, organization_id, key in rows:
        session.add(
            TriageLabel(ticket_id=ticket_id, organization_id=organization_id, category_key=key)
        )
    await session.commit()
    return len(rows)


# --- Staff ------------------------------------------------------------------------------


async def get_triage(
    session: AsyncSession, member: OrganizationMember, ticket_id: uuid.UUID
) -> tuple[TicketTriage | None, User | None]:
    require_role(member, *STAFF_ROLES)
    if await TicketRepository(session).get(member.organization_id, ticket_id) is None:
        raise NotFoundError("Ticket not found")
    row = await session.scalar(select(TicketTriage).where(TicketTriage.ticket_id == ticket_id))
    reviewer = await session.get(User, row.reviewed_by_id) if row and row.reviewed_by_id else None
    return row, reviewer


async def accept_suggestion(
    session: AsyncSession,
    member: OrganizationMember,
    user: User,
    ticket_id: uuid.UUID,
    events: EventBroker | None = None,
    notifications: NotificationService | None = None,
) -> TicketTriage:
    """A staff member agrees with the prediction: its category is set (if it isn't
    already) and the ticket becomes a label for the neighbour vote."""
    require_role(member, *STAFF_ROLES)
    service = TicketService(session, events, notifications)
    ticket = await service.get(member, user, ticket_id, for_update=True)
    row = await session.scalar(select(TicketTriage).where(TicketTriage.ticket_id == ticket.id))
    if row is None or row.category_id is None:
        raise NotFoundError("This ticket has no category suggestion", code="no_suggestion")
    if ticket.status == TicketStatus.CLOSED:
        raise ConflictError("Closed tickets can't be changed", code="ticket_closed")
    if ticket.category_id != row.category_id:
        await service._set_category(ticket, user.id, row.category_id)
    else:  # applied by triage: confirming it is the review
        await record_category_choice(session, ticket, ticket.category, user.id)
    await session.commit()
    await service.publish_change(ticket, customer_visible=False)
    return row


# --- Jobs ---------------------------------------------------------------------------------


async def after_ticket_opened(
    triage: TriageQueue, routing: RoutingQueue, member: OrganizationMember, ticket: Ticket
) -> None:
    """After a ticket is committed: triage it first if the organization uses triage and
    nobody chose a category (the triage job routes it afterwards), else route it."""
    if triage_mode(member.organization.settings) != "off" and ticket.category_id is None:
        try:
            await triage.enqueue(ticket.id)
            return
        except QueueUnavailableError as exc:
            logger.warning("Could not queue triage for ticket %s: %s", ticket.id, exc)
    await enqueue_new_ticket(routing, member, ticket)


async def triage_and_route(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    *,
    llm: LLMClient,
    embedder: Embedder,
    presence: PresenceTracker,
    events: EventBroker | None = None,
    mailer: NotificationMailer | None = None,
) -> TicketTriage | None:
    """The job for a newly opened ticket, as a system operation: triage, then routing
    (which then sees the category). A triage failure never stops routing."""
    await set_rls_context(session, bypass=True)
    row = (
        await session.execute(
            select(Ticket.organization_id, Organization.settings)
            .join(Organization, Organization.id == Ticket.organization_id)
            .where(Ticket.id == ticket_id)
        )
    ).first()
    if row is None:
        return None
    organization_id, organization_settings = row
    result = None
    mode = triage_mode(organization_settings)
    if mode != "off":
        try:
            result = await TriageService(session, llm, embedder, events).triage(
                organization_id, ticket_id, mode
            )
        except Exception:  # route the ticket anyway
            logger.exception("Triage failed for ticket %s", ticket_id)
            await session.rollback()
            await set_rls_context(session, bypass=True)
    await route_new_ticket(session, ticket_id, presence=presence, events=events, mailer=mailer)
    return result


async def run_triage_job(ticket_id: uuid.UUID) -> str | None:
    from app.core.knowledge import get_embedder
    from app.core.llm import get_llm_client

    async def job(
        session: AsyncSession,
        presence: PresenceTracker,
        events: EventBroker,
        mailer: NotificationMailer,
    ) -> str | None:
        row = await triage_and_route(
            session,
            ticket_id,
            llm=get_llm_client(),
            embedder=get_embedder(),
            presence=presence,
            events=events,
            mailer=mailer,
        )
        return row.decision.value if row else None

    return await with_worker_context(job)
