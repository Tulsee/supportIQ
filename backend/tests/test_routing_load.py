"""Routing and the SLA monitor under concurrency (Module 10, phase 8), with real
transactions on separate connections: many routing runs at once never push an agent
past capacity, two sweeps route each ticket once, and two SLA monitors record each
transition once. Data is committed for real and removed afterwards."""

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import set_rls_context
from app.core.security import utcnow
from app.models import (
    AgentProfile,
    Customer,
    Organization,
    OrganizationMember,
    OrgRole,
    RoutingRule,
    RoutingStrategy,
    RoutingTrigger,
    SlaClockKind,
    SlaClockState,
    Team,
    TeamMember,
    TeamRole,
    Ticket,
    TicketPriority,
    TicketSlaClock,
    TicketStatus,
    User,
)
from app.services.routing import RoutingService, sweep_unrouted
from app.services.sla_monitor import evaluate_sla_clocks
from app.websocket.presence import InMemoryPresence

pytestmark = pytest.mark.anyio

Factory = async_sessionmaker[AsyncSession]


@dataclass
class Org:
    id: uuid.UUID
    admin_id: uuid.UUID
    agent_ids: list[uuid.UUID]
    ticket_ids: list[uuid.UUID]
    presence: InMemoryPresence = field(default_factory=InMemoryPresence)


@asynccontextmanager
async def committed_org(
    session_factory: Factory,
    *,
    agents: int,
    capacity: int,
    tickets: int,
    opened_minutes_ago: int = 0,
    overdue_clocks: bool = False,
) -> AsyncIterator[Org]:
    """An organization with routing on, a catch-all least-loaded rule to one team of
    ``agents`` (each with room for ``capacity`` tickets, all online), and ``tickets``
    open, unassigned tickets (optionally each with a first-response clock past due)."""
    org_id, admin_id, team_id, customer_id = (uuid.uuid4() for _ in range(4))
    agent_ids = [uuid.uuid4() for _ in range(agents)]
    ticket_ids = [uuid.uuid4() for _ in range(tickets)]
    now = utcnow()
    opened = now - timedelta(minutes=opened_minutes_ago)
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        for user_id in (admin_id, *agent_ids):
            session.add(
                User(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    password_hash="x",
                    full_name=f"U {user_id.hex[:6]}",
                )
            )
        session.add(
            Organization(
                id=org_id,
                name="Load Co",
                slug=f"load-{org_id.hex[:10]}",
                settings={"routing_mode": "on"},
            )
        )
        await session.flush()
        session.add(
            OrganizationMember(organization_id=org_id, user_id=admin_id, role=OrgRole.ADMIN)
        )
        session.add(
            Team(id=team_id, organization_id=org_id, name="All", description="", is_active=True)
        )
        session.add(Customer(id=customer_id, organization_id=org_id, name="Cy"))
        await session.flush()
        for agent_id in agent_ids:
            session.add(
                OrganizationMember(organization_id=org_id, user_id=agent_id, role=OrgRole.AGENT)
            )
            session.add(
                TeamMember(
                    organization_id=org_id, team_id=team_id, user_id=agent_id, role=TeamRole.MEMBER
                )
            )
            session.add(
                AgentProfile(organization_id=org_id, user_id=agent_id, max_active_tickets=capacity)
            )
        session.add(
            RoutingRule(
                organization_id=org_id,
                name="Everything",
                position=1,
                conditions={},
                team_id=team_id,
                strategy=RoutingStrategy.LEAST_LOADED,
                required_skills=[],
                is_active=True,
                version=1,
            )
        )
        for number, ticket_id in enumerate(ticket_ids, 1):
            session.add(
                Ticket(
                    id=ticket_id,
                    organization_id=org_id,
                    number=number,
                    customer_id=customer_id,
                    subject=f"Load {number}",
                    status=TicketStatus.OPEN,
                    priority=TicketPriority.NORMAL,
                    created_at=opened,
                )
            )
        await session.flush()
        if overdue_clocks:
            for ticket_id in ticket_ids:
                session.add(
                    TicketSlaClock(
                        organization_id=org_id,
                        ticket_id=ticket_id,
                        kind=SlaClockKind.FIRST_RESPONSE,
                        cycle=1,
                        policy_name="Normal",
                        policy_version=1,
                        target_minutes=60,
                        warning_minutes=30,
                        calendar_snapshot={
                            "timezone": "Asia/Kathmandu",
                            "hours": {},
                            "holidays": [],
                        },
                        state=SlaClockState.RUNNING,
                        started_at=now - timedelta(hours=3),
                        warn_at=now - timedelta(hours=2),
                        due_at=now - timedelta(hours=1),
                    )
                )
        await session.commit()
    org = Org(org_id, admin_id, agent_ids, ticket_ids)
    for agent_id in agent_ids:
        await org.presence.touch(org_id, agent_id)
    try:
        yield org
    finally:
        async with session_factory() as session:
            await set_rls_context(session, bypass=True)
            await session.execute(delete(Ticket).where(Ticket.organization_id == org_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.execute(delete(User).where(User.id.in_([admin_id, *agent_ids])))
            await session.commit()


async def count(session_factory: Factory, sql: str, **params: object) -> int:
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        return (await session.execute(text(sql), params)).scalar_one()


async def test_a_burst_of_tickets_never_exceeds_anyones_capacity(session_factory: Factory) -> None:
    async with committed_org(session_factory, agents=3, capacity=2, tickets=12) as org:

        async def route(ticket_id: uuid.UUID):
            async with session_factory() as session:
                await set_rls_context(session, bypass=True)
                return await RoutingService(session, org.presence).route(
                    org.id, ticket_id, trigger=RoutingTrigger.CREATED
                )

        started = time.perf_counter()
        decisions = await asyncio.gather(*(route(t) for t in org.ticket_ids))
        elapsed = time.perf_counter() - started

        outcomes = Counter(d.outcome for d in decisions)
        assert outcomes == {"assigned": 6, "no_agent": 6}
        # Everyone got exactly their capacity, nobody more.
        per_agent = Counter(d.assignee_id for d in decisions if d.assignee_id)
        assert sorted(per_agent.values()) == [2, 2, 2]
        async with session_factory() as session:
            await set_rls_context(session, bypass=True)
            active = Counter(
                (
                    await session.scalars(
                        select(Ticket.assignee_id).where(
                            Ticket.organization_id == org.id, Ticket.assignee_id.is_not(None)
                        )
                    )
                ).all()
            )
        assert sorted(active.values()) == [2, 2, 2]
        print(f"12 concurrent routing runs: {elapsed:.2f} s")


async def test_two_sweeps_at_once_route_each_ticket_once(session_factory: Factory) -> None:
    async with committed_org(
        session_factory, agents=3, capacity=10, tickets=15, opened_minutes_ago=5
    ) as org:

        async def sweep() -> int:
            async with session_factory() as session:
                return await sweep_unrouted(session, presence=org.presence, batch=100)

        routed = await asyncio.gather(sweep(), sweep())
        assert sum(routed) == 15
        per_ticket = await count(
            session_factory,
            "SELECT count(*) FROM (SELECT ticket_id FROM routing_decisions "
            "WHERE organization_id = :org GROUP BY ticket_id HAVING count(*) > 1) x",
            org=org.id,
        )
        assert per_ticket == 0
        assigned = await count(
            session_factory,
            "SELECT count(*) FROM tickets WHERE organization_id = :org AND assignee_id IS NOT NULL",
            org=org.id,
        )
        assert assigned == 15


async def test_two_sla_monitors_at_once_record_each_breach_once(session_factory: Factory) -> None:
    async with committed_org(
        session_factory, agents=1, capacity=10, tickets=20, overdue_clocks=True
    ) as org:

        async def monitor():
            async with session_factory() as session:
                return await evaluate_sla_clocks(session, batch=5)

        results = await asyncio.gather(monitor(), monitor())
        assert sum(r.breached for r in results) == 20
        events = await count(
            session_factory,
            "SELECT count(*) FROM ticket_events "
            "WHERE organization_id = :org AND type = 'sla_breached'",
            org=org.id,
        )
        notices = await count(
            session_factory,
            "SELECT count(*) FROM notifications "
            "WHERE organization_id = :org AND kind = 'sla_breached'",
            org=org.id,
        )
        assert (events, notices) == (20, 20)  # one per clock, to the one admin
