"""Automatic routing (Module 10, phase 6).

``RuleService``: admins manage ordered rules (validated, audited).

``RoutingService.route`` routes one ticket:

1. Lock the ticket. Stop if someone is assigned (a person's choice always wins), it's
   resolved or closed, or, for automatic triggers, a person already chose its team or
   it already has an automatic decision. Together with the lock, that's what stops
   two runs from routing one ticket twice; a unique index backs it up.
2. Match the first active rule (``app/domain/routing.py``); none: ``no_rule``.
3. Strategy ``manual``: set the team only (``team_only``).
4. Rank the team's agents: available, online, under capacity, with the required
   skills, in the strategy's order.
5. Claim the first one: lock their profile row, count their active tickets again,
   and assign if they still have room. Concurrent runs for different tickets wait on
   that lock, so nobody is pushed past capacity. Nobody left: ``no_agent``, and the
   team's leads (or the admins) are notified.
6. Record the decision (rules tried, candidates, explanation) and, unless in shadow
   mode, apply it through ``TicketService`` (timeline events, notifications).

Triggers: the job enqueued when a ticket is opened, a sweep for tickets whose job
never ran, and a person's "reroute". The simulator runs steps 2-4 and writes nothing.
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, joinedload

from app.core.config import settings
from app.core.database import set_rls_context
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import utcnow
from app.domain.routing import (
    AgentFacts,
    Candidate,
    Condition,
    Outcome,
    Rule,
    RuleCheck,
    Strategy,
    TicketFacts,
    explain,
    match_rule,
    rank,
)
from app.models import (
    DEFAULT_MAX_ACTIVE_TICKETS,
    AgentProfile,
    AgentSkill,
    AgentStatus,
    Customer,
    NotificationKind,
    Organization,
    OrganizationMember,
    OrgRole,
    RoutingDecision,
    RoutingRule,
    RoutingTrigger,
    Team,
    TeamMember,
    Ticket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    User,
)
from app.repositories.tickets import TicketRepository
from app.schemas.organizations import OrganizationSettings, RoutingMode
from app.schemas.routing import (
    CandidateRead,
    Named,
    RuleCheckRead,
    RuleCreate,
    RuleOrder,
    RuleUpdate,
    SimulationRead,
    SimulationRequest,
)
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.services.notifications import NewNotification, NotificationService
from app.services.teams import active_ticket_counts, escalation_contacts, lock_agent_profile
from app.services.tickets import TicketService
from app.websocket.broker import EventBroker
from app.websocket.presence import PresenceTracker
from app.workers.context import with_worker_context
from app.workers.queue import NotificationMailer, QueueUnavailableError, RoutingQueue

logger = logging.getLogger(__name__)

RULE_INVALID = "routing_rule_invalid"
# Metadata keys a rule can name (``customer.<key>``).
CUSTOMER_KEY_PATTERN = "^[a-z0-9_]{1,50}$"
# The sweep leaves older tickets alone: turning routing on doesn't route the backlog.
SWEEP_MAX_AGE = timedelta(hours=24)
SWEEP_BATCH = 100


def routing_mode(organization_settings: dict[str, Any] | None) -> RoutingMode:
    return OrganizationSettings.model_validate(organization_settings or {}).routing_mode


def is_routable(ticket: Ticket) -> bool:
    return ticket.assignee_id is None and ticket.status not in (
        TicketStatus.RESOLVED,
        TicketStatus.CLOSED,
    )


def ticket_facts(ticket: Ticket) -> TicketFacts:
    """Category, customer and team loaded."""
    return TicketFacts(
        category=ticket.category.key if ticket.category else None,
        priority=ticket.priority.value,
        subject=ticket.subject,
        customer=ticket.customer.metadata_ or {},
        language=ticket.language,
    )


# --- Rules ----------------------------------------------------------------------------------


class RuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_rules(self, member: OrganizationMember) -> list[RoutingRule]:
        require_role(member, *STAFF_ROLES)
        rows = await self.session.scalars(
            select(RoutingRule)
            .options(joinedload(RoutingRule.team))
            .where(RoutingRule.organization_id == member.organization_id)
            .order_by(RoutingRule.position, RoutingRule.created_at)
        )
        return list(rows.all())

    async def create(self, member: OrganizationMember, data: RuleCreate) -> RoutingRule:
        require_role(member, OrgRole.ADMIN)
        team = await self._active_team(member, data.team_id)
        conditions = data.conditions.model_dump(mode="json")
        await self._check_categories(member, conditions)
        last = await self.session.scalar(
            select(func.max(RoutingRule.position)).where(
                RoutingRule.organization_id == member.organization_id
            )
        )
        rule = RoutingRule(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            name=data.name,
            position=(last or 0) + 1,
            conditions=conditions,
            team_id=team.id,
            strategy=data.strategy,
            required_skills=[s.model_dump() for s in data.required_skills],
            is_active=data.is_active,
            version=1,
        )
        rule.team = team
        self.session.add(rule)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="routing_rule",
            entity_id=rule.id,
            action="routing_rule.created",
            summary=f"Added the routing rule {data.name} (to {team.name}, {data.strategy})",
        )
        await self._commit_or_conflict()
        return rule

    async def update(
        self, member: OrganizationMember, rule_id: uuid.UUID, data: RuleUpdate
    ) -> RoutingRule:
        require_role(member, OrgRole.ADMIN)
        rule = await self._get(member, rule_id)
        wanted: dict[str, Any] = {}
        if data.name is not None:
            wanted["name"] = data.name
        if data.conditions is not None:
            wanted["conditions"] = data.conditions.model_dump(mode="json")
            await self._check_categories(member, wanted["conditions"])
        if data.team_id is not None:
            if data.team_id != rule.team_id:
                rule.team = await self._active_team(member, data.team_id)
            wanted["team_id"] = data.team_id
        if data.strategy is not None:
            wanted["strategy"] = data.strategy
        if data.required_skills is not None:
            wanted["required_skills"] = [s.model_dump() for s in data.required_skills]
        if data.is_active is not None:
            wanted["is_active"] = data.is_active
        changes = {
            key: {"from": _jsonable(getattr(rule, key)), "to": _jsonable(value)}
            for key, value in wanted.items()
            if getattr(rule, key) != value
        }
        if not changes:
            return rule
        for key in changes:
            setattr(rule, key, wanted[key])
        if changes.keys() & {"conditions", "team_id", "strategy", "required_skills"}:
            rule.version += 1
        if set(changes) == {"is_active"}:
            verb = "Turned on" if rule.is_active else "Turned off"
            summary = f"{verb} the routing rule {rule.name}"
        else:
            summary = f"Updated the routing rule {rule.name}"
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="routing_rule",
            entity_id=rule.id,
            action="routing_rule.updated",
            summary=summary,
            changes=changes,
        )
        await self._commit_or_conflict()
        return rule

    async def delete(self, member: OrganizationMember, rule_id: uuid.UUID) -> None:
        """Past decisions keep the rule's name and version."""
        require_role(member, OrgRole.ADMIN)
        rule = await self._get(member, rule_id)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="routing_rule",
            entity_id=rule.id,
            action="routing_rule.deleted",
            summary=f"Deleted the routing rule {rule.name}",
        )
        await self.session.delete(rule)
        await self.session.commit()

    async def reorder(self, member: OrganizationMember, data: RuleOrder) -> list[RoutingRule]:
        require_role(member, OrgRole.ADMIN)
        rules = list(
            (
                await self.session.scalars(
                    select(RoutingRule)
                    .options(joinedload(RoutingRule.team))
                    .where(RoutingRule.organization_id == member.organization_id)
                    .order_by(RoutingRule.position, RoutingRule.created_at)
                    .with_for_update(of=RoutingRule)
                )
            ).all()
        )
        by_id = {r.id: r for r in rules}
        if len(data.rule_ids) != len(set(data.rule_ids)) or set(data.rule_ids) != set(by_id):
            raise AppError("List every routing rule exactly once", code=RULE_INVALID)
        before = [r.name for r in rules]
        for position, rule_id in enumerate(data.rule_ids, 1):
            by_id[rule_id].position = position
        after = [by_id[i].name for i in data.rule_ids]
        if before != after:
            await audit.record(
                self.session,
                organization_id=member.organization_id,
                actor_id=member.user_id,
                entity_type="routing_rule",
                entity_id=None,
                action="routing_rule.reordered",
                summary="Changed the order of the routing rules",
                changes={"order": {"from": before, "to": after}},
            )
        await self.session.commit()
        return [by_id[i] for i in data.rule_ids]

    # --- Helpers ----------------------------------------------------------------------

    async def customer_fields(self, member: OrganizationMember) -> list[str]:
        """Keys of customers' metadata that rules can use, most common first."""
        require_role(member, *STAFF_ROLES)
        key = func.jsonb_object_keys(Customer.metadata_).table_valued("value").render_derived()
        rows = await self.session.execute(
            select(key.c.value, func.count())
            .select_from(Customer)
            .join(key, true())
            .where(
                Customer.organization_id == member.organization_id,
                key.c.value.op("~")(CUSTOMER_KEY_PATTERN),
            )
            .group_by(key.c.value)
            .order_by(func.count().desc(), key.c.value)
            .limit(50)
        )
        return [name for name, _ in rows.all()]

    async def _get(self, member: OrganizationMember, rule_id: uuid.UUID) -> RoutingRule:
        rule = await self.session.scalar(
            select(RoutingRule)
            .options(joinedload(RoutingRule.team))
            .where(RoutingRule.id == rule_id, RoutingRule.organization_id == member.organization_id)
            .with_for_update(of=RoutingRule)
        )
        if rule is None:
            raise NotFoundError("Routing rule not found")
        return rule

    async def _active_team(self, member: OrganizationMember, team_id: uuid.UUID) -> Team:
        team = await self.session.get(Team, team_id)
        if team is None or team.organization_id != member.organization_id or not team.is_active:
            raise AppError("Choose an active team of this organization", code=RULE_INVALID)
        return team

    async def _check_categories(
        self, member: OrganizationMember, conditions: dict[str, Any]
    ) -> None:
        """Category conditions name existing categories (by key)."""
        keys: set[str] = set()
        for condition in [*conditions.get("all", []), *conditions.get("any", [])]:
            if condition["field"] == "category" and condition["operator"] != "is_set":
                value = condition["value"]
                keys.update(value if isinstance(value, list) else [value])
        if not keys:
            return
        known = set(
            (
                await self.session.scalars(
                    select(TicketCategory.key).where(
                        TicketCategory.organization_id == member.organization_id,
                        TicketCategory.key.in_(keys),
                    )
                )
            ).all()
        )
        if unknown := sorted(keys - known):
            raise AppError(
                f"Unknown categories: {', '.join(unknown)}",
                code=RULE_INVALID,
                details={"unknown_categories": unknown},
            )

    async def _commit_or_conflict(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # the case-insensitive unique name index
            await self.session.rollback()
            raise ConflictError(
                "A routing rule with this name already exists", code="routing_rule_exists"
            ) from exc


def _jsonable(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


# --- Routing --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    rule: Rule | None
    checks: list[RuleCheck]
    candidates: list[Candidate]
    outcome: Outcome
    assignee: Candidate | None


class RoutingService:
    def __init__(
        self,
        session: AsyncSession,
        presence: PresenceTracker,
        events: EventBroker | None = None,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.presence = presence
        self.notifications = notifications
        self.tickets = TicketRepository(session)
        self.ticket_service = TicketService(session, events, notifications)

    # --- Routing a ticket -------------------------------------------------------------

    async def route(
        self,
        organization_id: uuid.UUID,
        ticket_id: uuid.UUID,
        *,
        trigger: RoutingTrigger,
        actor_id: uuid.UUID | None = None,
        apply: bool = True,
    ) -> RoutingDecision | None:
        """Route one ticket and commit. None when there was nothing to do: assigned,
        resolved or closed meanwhile, or (automatic triggers) its team chosen by a
        person or already routed automatically."""
        ticket = await self.tickets.get(organization_id, ticket_id, for_update=True)
        if ticket is None or not is_routable(ticket):
            return None
        if trigger != RoutingTrigger.REROUTE and (
            # Before any automatic run, only a person can have set the team.
            ticket.team_id is not None or await self._routed_automatically(ticket.id)
        ):
            return None

        plan = await self._plan(organization_id, ticket_facts(ticket), claim=apply)
        rule = plan.rule
        decision = RoutingDecision(
            id=uuid.uuid4(),
            organization_id=organization_id,
            ticket_id=ticket.id,
            trigger=trigger,
            applied=apply,
            actor_id=actor_id,
            rule_id=rule.id if rule else None,
            rule_name=rule.name if rule else None,
            rule_version=rule.version if rule else None,
            team_id=rule.team_id if rule else None,
            team_name=rule.team_name if rule else None,
            strategy=rule.strategy if rule else None,
            outcome=plan.outcome,
            assignee_id=plan.assignee.user_id if plan.assignee else None,
            assignee_name=plan.assignee.name if plan.assignee else None,
            rules=[c.to_json() for c in plan.checks],
            candidates=[c.to_json() for c in plan.candidates],
            explanation=explain(
                rule=rule,
                checks=plan.checks,
                candidates=plan.candidates,
                outcome=plan.outcome,
                assignee=plan.assignee,
                applied=apply,
            ),
        )
        self.session.add(decision)
        await self.session.flush()

        customer_visible = False
        if apply and rule is not None:
            customer_visible = await self.ticket_service.apply_routing(
                ticket,
                actor_id,
                team_id=rule.team_id,
                assignee_id=plan.assignee.user_id if plan.assignee else None,
                details={"source": "routing", "decision_id": str(decision.id), "rule": rule.name},
            )
            if plan.assignee is not None:
                profile = await lock_agent_profile(
                    self.session, organization_id, plan.assignee.user_id
                )
                profile.last_assigned_at = utcnow()
            elif plan.outcome == Outcome.NO_AGENT:
                await self._flag(ticket, decision, rule, actor_id)
        await self.session.commit()
        if apply and rule is not None:
            await self.ticket_service.publish_change(ticket, customer_visible=customer_visible)
        logger.info(
            "Routed ticket %s (%s): %s%s",
            ticket.id,
            trigger,
            plan.outcome,
            "" if apply else " (shadow)",
        )
        return decision

    async def reroute(
        self, member: OrganizationMember, user: User, ticket_id: uuid.UUID
    ) -> RoutingDecision:
        """A staff member runs routing again now (whatever the organization's mode)."""
        require_role(member, *STAFF_ROLES)
        ticket = await self.tickets.get(member.organization_id, ticket_id, for_update=True)
        if ticket is None:
            raise NotFoundError("Ticket not found")
        if ticket.assignee_id is not None:
            raise ConflictError(
                "This ticket is assigned. Unassign it first: routing never replaces a "
                "person's choice.",
                code="not_routable",
            )
        if not is_routable(ticket):
            raise ConflictError("Resolved and closed tickets aren't routed", code="not_routable")
        decision = await self.route(
            member.organization_id, ticket_id, trigger=RoutingTrigger.REROUTE, actor_id=user.id
        )
        assert decision is not None  # routable, and reroutes always run
        return decision

    async def simulate(
        self, member: OrganizationMember, request: SimulationRequest
    ) -> SimulationRead:
        """What routing would do now, for a real or a hypothetical ticket. No writes."""
        require_role(member, OrgRole.ADMIN)
        if request.ticket_id is not None:
            ticket = await self.tickets.get(member.organization_id, request.ticket_id)
            if ticket is None:
                raise NotFoundError("Ticket not found")
            facts = ticket_facts(ticket)
        else:
            facts = TicketFacts(
                category=request.category.strip().lower() if request.category else None,
                priority=(request.priority or TicketPriority.NORMAL).value,
                subject=request.subject or "",
                customer=request.customer,
            )
        plan = await self._plan(member.organization_id, facts, claim=False)
        rule = plan.rule
        candidates = [CandidateRead.model_validate(c.to_json()) for c in plan.candidates]
        return SimulationRead(
            outcome=plan.outcome,
            rule=Named(id=rule.id, name=rule.name) if rule else None,
            team=Named(id=rule.team_id, name=rule.team_name) if rule else None,
            strategy=rule.strategy if rule else None,
            assignee=next((c for c in candidates if c.rank == 1), None),
            rules=[RuleCheckRead.model_validate(c.to_json()) for c in plan.checks],
            candidates=candidates,
            explanation=explain(
                rule=rule,
                checks=plan.checks,
                candidates=plan.candidates,
                outcome=plan.outcome,
                assignee=plan.assignee,
                applied=False,
            ),
        )

    async def decisions(
        self, member: OrganizationMember, ticket_id: uuid.UUID
    ) -> list[tuple[RoutingDecision, User | None]]:
        """The ticket's routing decisions, newest first, with who asked for each."""
        require_role(member, *STAFF_ROLES)
        if await self.tickets.get(member.organization_id, ticket_id) is None:
            raise NotFoundError("Ticket not found")
        rows = list(
            (
                await self.session.scalars(
                    select(RoutingDecision)
                    .where(
                        RoutingDecision.organization_id == member.organization_id,
                        RoutingDecision.ticket_id == ticket_id,
                    )
                    .order_by(RoutingDecision.seq.desc())
                )
            ).all()
        )
        actor_ids = {d.actor_id for d in rows if d.actor_id}
        actors = {}
        if actor_ids:
            users = await self.session.scalars(select(User).where(User.id.in_(actor_ids)))
            actors = {u.id: u for u in users.all()}
        return [(d, actors.get(d.actor_id) if d.actor_id else None) for d in rows]

    # --- Helpers ----------------------------------------------------------------------

    async def _plan(self, organization_id: uuid.UUID, facts: TicketFacts, *, claim: bool) -> Plan:
        rule, checks = match_rule(await self._rules(organization_id), facts)
        if rule is None:
            return Plan(None, checks, [], Outcome.NO_RULE, None)
        if rule.strategy == Strategy.MANUAL:
            return Plan(rule, checks, [], Outcome.TEAM_ONLY, None)
        candidates = rank(
            await self._agents(organization_id, rule.team_id),
            rule.strategy,
            rule.required_skills,
        )
        if claim:
            candidates, assignee = await self._claim(organization_id, candidates)
        else:
            assignee = next((c for c in candidates if c.eligible), None)
        outcome = Outcome.ASSIGNED if assignee else Outcome.NO_AGENT
        return Plan(rule, checks, candidates, outcome, assignee)

    async def _rules(self, organization_id: uuid.UUID) -> list[Rule]:
        """Active rules whose team is active, in order."""
        rows = await self.session.scalars(
            select(RoutingRule)
            .join(Team, Team.id == RoutingRule.team_id)
            .options(contains_eager(RoutingRule.team))
            .where(
                RoutingRule.organization_id == organization_id,
                RoutingRule.is_active.is_(True),
                Team.is_active.is_(True),
            )
            .order_by(RoutingRule.position, RoutingRule.created_at)
        )
        return [
            Rule(
                id=r.id,
                name=r.name,
                version=r.version,
                all=tuple(Condition.from_json(c) for c in r.conditions.get("all", [])),
                any=tuple(Condition.from_json(c) for c in r.conditions.get("any", [])),
                team_id=r.team_id,
                team_name=r.team.name,
                strategy=Strategy(r.strategy),
                required_skills=tuple((s["skill"], int(s["level"])) for s in r.required_skills),
            )
            for r in rows.all()
        ]

    async def _agents(self, organization_id: uuid.UUID, team_id: uuid.UUID) -> list[AgentFacts]:
        """The team's members who are still staff, with what routing needs to know."""
        members = (
            await self.session.execute(
                select(User.id, User.full_name)
                .join(TeamMember, TeamMember.user_id == User.id)
                .join(
                    OrganizationMember,
                    (OrganizationMember.user_id == User.id)
                    & (OrganizationMember.organization_id == organization_id),
                )
                .where(
                    TeamMember.team_id == team_id,
                    TeamMember.organization_id == organization_id,
                    OrganizationMember.role.in_(STAFF_ROLES),
                )
            )
        ).all()
        ids = [user_id for user_id, _ in members]
        if not ids:
            return []
        profiles = {
            p.user_id: p
            for p in (
                await self.session.scalars(
                    select(AgentProfile).where(
                        AgentProfile.organization_id == organization_id,
                        AgentProfile.user_id.in_(ids),
                    )
                )
            ).all()
        }
        skills: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
        for row in (
            await self.session.scalars(
                select(AgentSkill).where(
                    AgentSkill.organization_id == organization_id, AgentSkill.user_id.in_(ids)
                )
            )
        ).all():
            skills[row.user_id][row.skill] = row.level
        workload = await active_ticket_counts(self.session, organization_id, ids)
        online = await self.presence.online(organization_id)
        agents = []
        for user_id, name in members:
            profile = profiles.get(user_id)
            agents.append(
                AgentFacts(
                    user_id=user_id,
                    name=name,
                    status=(profile.status if profile else AgentStatus.AVAILABLE).value,
                    online=user_id in online,
                    active_tickets=workload.get(user_id, 0),
                    capacity=profile.max_active_tickets if profile else DEFAULT_MAX_ACTIVE_TICKETS,
                    skills=skills[user_id],
                    last_assigned_at=profile.last_assigned_at if profile else None,
                )
            )
        return agents

    async def _claim(
        self, organization_id: uuid.UUID, candidates: Sequence[Candidate]
    ) -> tuple[list[Candidate], Candidate | None]:
        """Take the first eligible agent who, with their profile locked, still has
        room. Agents who lost it meanwhile are marked with the reason."""
        result = list(candidates)
        for index, candidate in enumerate(result):
            if not candidate.eligible:
                continue
            profile = await lock_agent_profile(self.session, organization_id, candidate.user_id)
            active = (
                await active_ticket_counts(self.session, organization_id, [candidate.user_id])
            ).get(candidate.user_id, 0)
            reasons = []
            if profile.status != AgentStatus.AVAILABLE:
                reasons.append(profile.status.value)
            if profile.max_active_tickets <= 0:
                reasons.append("takes no automatic assignments")
            elif active >= profile.max_active_tickets:
                reasons.append(f"at capacity, {active} of {profile.max_active_tickets}")
            if not reasons:
                return result, candidate
            result[index] = replace(
                candidate,
                reasons=tuple(reasons),
                rank=None,
                active_tickets=active,
                capacity=profile.max_active_tickets,
            )
        return result, None

    async def _routed_automatically(self, ticket_id: uuid.UUID) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        RoutingDecision.ticket_id == ticket_id,
                        RoutingDecision.trigger != RoutingTrigger.REROUTE,
                    )
                )
            )
        )

    async def _flag(
        self,
        ticket: Ticket,
        decision: RoutingDecision,
        rule: Rule,
        actor_id: uuid.UUID | None,
    ) -> None:
        """Nobody could take it: tell the team's leads (or the admins)."""
        if self.notifications is None:
            return
        recipients = await escalation_contacts(self.session, ticket.organization_id, rule.team_id)
        await self.notifications.add(
            ticket.organization_id,
            [
                NewNotification(
                    user_id=user_id,
                    kind=NotificationKind.ROUTING_NO_AGENT,
                    title=f"Nobody in {rule.team_name} can take #{ticket.number}",
                    body=f"{ticket.subject} · it stays unassigned until someone picks it up",
                    ticket_id=ticket.id,
                    data={"decision_id": str(decision.id), "team": rule.team_name},
                    dedupe_key=f"routing_no_agent:{decision.id}",
                )
                for user_id in recipients
            ],
            actor_id=actor_id,
        )


# --- Jobs -------------------------------------------------------------------------------


async def enqueue_new_ticket(
    queue: RoutingQueue, member: OrganizationMember, ticket: Ticket
) -> None:
    """After a ticket is opened (and committed): queue its routing job, if the
    organization routes automatically and nobody was assigned yet. Best effort: if
    the broker is down, the sweep routes it a little later."""
    if (
        ticket.assignee_id is not None
        or ticket.team_id is not None  # a person chose the team: theirs to hand out
        or routing_mode(member.organization.settings) == "off"
    ):
        return
    try:
        await queue.enqueue(ticket.id)
    except QueueUnavailableError as exc:
        logger.warning("Could not queue routing for ticket %s (the sweep will): %s", ticket.id, exc)


async def route_new_ticket(
    session: AsyncSession,
    ticket_id: uuid.UUID,
    *,
    presence: PresenceTracker,
    events: EventBroker | None = None,
    mailer: NotificationMailer | None = None,
) -> RoutingDecision | None:
    """The routing job for a newly opened ticket, as a system operation."""
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
    mode = routing_mode(organization_settings)
    if mode == "off":
        return None
    notifications = NotificationService(session, events=events, mailer=mailer)
    return await RoutingService(session, presence, events, notifications).route(
        organization_id, ticket_id, trigger=RoutingTrigger.CREATED, apply=mode == "on"
    )


async def sweep_unrouted(
    session: AsyncSession,
    *,
    presence: PresenceTracker,
    events: EventBroker | None = None,
    mailer: NotificationMailer | None = None,
    now: datetime | None = None,
    delay_seconds: int | None = None,
    batch: int = SWEEP_BATCH,
) -> int:
    """Route open tickets with no assignee, no team and no routing decision, opened
    between a day ago and ``delay_seconds`` ago, in organizations that route
    automatically. Safe to
    run twice at once: each ticket is locked and re-checked before routing."""
    now = now or utcnow()
    delay = timedelta(
        seconds=settings.routing_sweep_delay_seconds if delay_seconds is None else delay_seconds
    )
    await set_rls_context(session, bypass=True)
    mode = Organization.settings["routing_mode"].astext
    rows = (
        await session.execute(
            select(Ticket.id, Ticket.organization_id, mode)
            .join(Organization, Organization.id == Ticket.organization_id)
            .where(
                Ticket.status == TicketStatus.OPEN,
                Ticket.assignee_id.is_(None),
                Ticket.team_id.is_(None),
                Ticket.created_at <= now - delay,
                Ticket.created_at > now - SWEEP_MAX_AGE,
                mode.in_(["on", "shadow"]),
                ~exists().where(RoutingDecision.ticket_id == Ticket.id),
            )
            .order_by(Ticket.created_at)
            .limit(batch)
        )
    ).all()
    await session.rollback()  # nothing held while routing
    notifications = NotificationService(session, events=events, mailer=mailer)
    service = RoutingService(session, presence, events, notifications)
    routed = 0
    for ticket_id, organization_id, org_mode in rows:
        decision = await service.route(
            organization_id, ticket_id, trigger=RoutingTrigger.SWEEP, apply=org_mode == "on"
        )
        routed += decision is not None
    if routed:
        logger.info("Routing sweep: routed %d tickets", routed)
    return routed


async def run_routing_job(ticket_id: uuid.UUID) -> str | None:
    async def job(
        session: AsyncSession,
        presence: PresenceTracker,
        events: EventBroker,
        mailer: NotificationMailer,
    ) -> str | None:
        decision = await route_new_ticket(
            session, ticket_id, presence=presence, events=events, mailer=mailer
        )
        return decision.outcome.value if decision else None

    return await with_worker_context(job)


async def run_routing_sweep() -> int:
    async def job(
        session: AsyncSession,
        presence: PresenceTracker,
        events: EventBroker,
        mailer: NotificationMailer,
    ) -> int:
        return await sweep_unrouted(session, presence=presence, events=events, mailer=mailer)

    return await with_worker_context(job)
