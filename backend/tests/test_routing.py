"""Automatic routing (Module 10, phase 6): rules, routing new tickets, the strategies
in practice, nobody available, manual teams, shadow mode, a person's choice winning,
rerouting, the simulator, the sweep, concurrency, and staff-only data."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import delete, text
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
    Team,
    TeamMember,
    TeamRole,
    Ticket,
    TicketPriority,
    TicketStatus,
    User,
)
from app.services.routing import RoutingService, route_new_ticket, sweep_unrouted
from app.websocket.broker import InMemoryEventBroker
from app.websocket.presence import InMemoryPresence
from tests.factories import (
    API,
    AuthedUser,
    Outbox,
    RecordingMailer,
    RecordingRoutingQueue,
    Workspace,
    add_member,
    create_ticket,
    create_user,
    in_org,
)
from tests.test_categories import add_category
from tests.test_teams import add_team

pytestmark = pytest.mark.anyio


@dataclass
class Crew:
    team: dict[str, Any]
    lead: AuthedUser


@pytest.fixture
async def crew(api: httpx2.AsyncClient, ws: Workspace, outbox: Outbox) -> Crew:
    """A Support team: the workspace's agent (Abe) and a lead (Lena)."""
    lead = await create_user(api, full_name="Lena Lead")
    await add_member(api, outbox, ws.admin, ws.org_id, lead, "agent")
    team = await add_team(
        api, ws, "Support", [{"user_id": ws.agent.id}, {"user_id": lead.id, "role": "lead"}]
    )
    return Crew(team, lead)


@dataclass
class Router:
    """Runs the routing job in-process, as a worker would."""

    session: AsyncSession
    presence: InMemoryPresence
    broker: InMemoryEventBroker
    mailer: RecordingMailer
    org_id: uuid.UUID

    async def new(self, ticket: dict[str, Any]):
        return await route_new_ticket(
            self.session,
            uuid.UUID(ticket["id"]),
            presence=self.presence,
            events=self.broker,
            mailer=self.mailer,
        )

    async def online(self, *users: AuthedUser) -> None:
        for user in users:
            await self.presence.touch(self.org_id, uuid.UUID(user.id))


@pytest.fixture
def router(
    db_session: AsyncSession,
    presence: InMemoryPresence,
    broker: InMemoryEventBroker,
    mailer: RecordingMailer,
    ws: Workspace,
) -> Router:
    return Router(db_session, presence, broker, mailer, uuid.UUID(ws.org_id))


async def set_mode(api: httpx2.AsyncClient, ws: Workspace, mode: str) -> None:
    response = await api.patch(
        f"{API}/organizations/{ws.org_id}",
        json={"settings": {"routing_mode": mode}},
        headers=ws.admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["routing_mode"] == mode


def condition(field: str, operator: str, value: Any) -> dict[str, Any]:
    return {"field": field, "operator": operator, "value": value}


async def add_rule(
    api: httpx2.AsyncClient,
    ws: Workspace,
    name: str,
    team_id: str,
    *,
    expect: int = 201,
    **extra: Any,
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/routing/rules",
        json={"name": name, "team_id": team_id, **extra},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == expect, response.text
    return response.json()


async def fetch(api: httpx2.AsyncClient, ws: Workspace, ticket: dict[str, Any]) -> dict[str, Any]:
    response = await api.get(f"{API}/tickets/{ticket['id']}", headers=in_org(ws.agent, ws.org_id))
    return response.json()


async def history(
    api: httpx2.AsyncClient, ws: Workspace, ticket: dict[str, Any], user: AuthedUser | None = None
) -> httpx2.Response:
    return await api.get(
        f"{API}/tickets/{ticket['id']}/routing", headers=in_org(user or ws.agent, ws.org_id)
    )


async def notices(api: httpx2.AsyncClient, ws: Workspace, user: AuthedUser) -> list[str]:
    items = (await api.get(f"{API}/notifications", headers=in_org(user, ws.org_id))).json()["items"]
    return [f"{i['kind']}: {i['title']}" for i in items]


# --- Rules ---------------------------------------------------------------------------------


async def test_admins_manage_ordered_rules(api: httpx2.AsyncClient, ws: Workspace) -> None:
    team = await add_team(api, ws, "Billing")
    await add_category(api, ws, "Billing")
    admin = in_org(ws.admin, ws.org_id)

    first = await add_rule(
        api, ws, "Billing", team["id"], conditions={"all": [condition("category", "eq", "Billing")]}
    )
    assert (first["position"], first["version"], first["strategy"]) == (1, 1, "least_loaded")
    assert first["conditions"]["all"] == [condition("category", "eq", "billing")]
    assert first["team"] == {"id": team["id"], "name": "Billing", "is_active": True}
    second = await add_rule(api, ws, "Everything else", team["id"], strategy="round_robin")
    assert second["position"] == 2

    duplicate = await add_rule(api, ws, " billing ", team["id"], expect=409)
    assert duplicate["error"]["code"] == "routing_rule_exists"
    unknown = await add_rule(
        api,
        ws,
        "Refunds",
        team["id"],
        conditions={"any": [condition("category", "in", ["billing", "refunds"])]},
        expect=400,
    )
    assert unknown["error"]["code"] == "routing_rule_invalid"
    assert unknown["error"]["details"] == {"unknown_categories": ["refunds"]}
    retired = await add_team(api, ws, "Retired")
    await api.patch(f"{API}/teams/{retired['id']}", json={"is_active": False}, headers=admin)
    inactive = await add_rule(api, ws, "Old", retired["id"], expect=400)
    assert inactive["error"]["code"] == "routing_rule_invalid"
    for bad in (
        condition("subject", "eq", "refund"),  # subject only takes contains
        condition("priority", "in", ["urgent", "asap"]),
        condition("category", "in", "billing"),  # in takes a list
        condition("customer.Plan", "eq", "gold"),  # keys are lower case
        condition("category", "is_set", "yes"),
        condition("body", "contains", "x"),  # message content is never a field
    ):
        await add_rule(api, ws, "Bad", team["id"], conditions={"all": [bad]}, expect=422)

    # A new name keeps the version; changing what the rule does bumps it.
    url = f"{API}/routing/rules/{first['id']}"
    renamed = (await api.patch(url, json={"name": "Billing questions"}, headers=admin)).json()
    assert renamed["version"] == 1
    changed = await api.patch(
        url,
        json={"strategy": "skill_match", "required_skills": [{"skill": "Billing", "level": 2}]},
        headers=admin,
    )
    assert changed.json()["version"] == 2
    assert changed.json()["required_skills"] == [{"skill": "billing", "level": 2}]

    reordered = await api.put(
        f"{API}/routing/rules/order",
        json={"rule_ids": [second["id"], first["id"]]},
        headers=admin,
    )
    assert [(r["name"], r["position"]) for r in reordered.json()] == [
        ("Everything else", 1),
        ("Billing questions", 2),
    ]
    partial = await api.put(
        f"{API}/routing/rules/order", json={"rule_ids": [first["id"]]}, headers=admin
    )
    assert partial.status_code == 400

    agent = in_org(ws.agent, ws.org_id)
    listed = await api.get(f"{API}/routing/rules", headers=agent)
    assert [r["name"] for r in listed.json()] == ["Everything else", "Billing questions"]
    assert (await api.post(f"{API}/routing/rules", json={}, headers=agent)).status_code in (
        403,
        422,
    )
    forbidden = await api.post(
        f"{API}/routing/rules", json={"name": "Mine", "team_id": team["id"]}, headers=agent
    )
    assert forbidden.status_code == 403
    customer = in_org(ws.customer, ws.org_id)
    assert (await api.get(f"{API}/routing/rules", headers=customer)).status_code == 403

    assert (
        await api.delete(f"{API}/routing/rules/{second['id']}", headers=admin)
    ).status_code == 204
    assert [r["name"] for r in (await api.get(f"{API}/routing/rules", headers=agent)).json()] == [
        "Billing questions"
    ]
    log = (await api.get(f"{API}/audit-log", headers=admin)).json()["items"]
    actions = [e["action"] for e in log if e["entity_type"] == "routing_rule"]
    assert actions == [
        "routing_rule.deleted",
        "routing_rule.reordered",
        "routing_rule.updated",
        "routing_rule.updated",
        "routing_rule.created",
        "routing_rule.created",
    ]


# --- Routing new tickets -------------------------------------------------------------------


async def test_new_tickets_go_to_the_least_loaded_available_agent(
    api: httpx2.AsyncClient,
    ws: Workspace,
    crew: Crew,
    router: Router,
    routing_queue: RecordingRoutingQueue,
    broker: InMemoryEventBroker,
) -> None:
    await add_rule(api, ws, "Everything", crew.team["id"])
    first = await create_ticket(api, ws.customer, ws.org_id)
    assert routing_queue.ids == []  # routing is off by default

    await set_mode(api, ws, "on")
    # Lena already works on one; tickets opened with an assignee aren't routed.
    await create_ticket(
        api, ws.agent, ws.org_id, customer_id=first["customer"]["id"], assignee_id=crew.lead.id
    )
    assert routing_queue.ids == []
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Router down")
    assert routing_queue.ids == [uuid.UUID(ticket["id"])]

    await router.online(ws.agent, crew.lead)
    broker.published.clear()
    decision = await router.new(ticket)
    assert decision is not None and decision.outcome == "assigned"

    routed = await fetch(api, ws, ticket)
    assert routed["assignee"]["full_name"] == "Abe Agent"
    assert (routed["team"]["name"], routed["status"]) == ("Support", "assigned")

    staff_events = (
        await api.get(f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.agent, ws.org_id))
    ).json()
    assigned = next(e for e in staff_events if e["type"] == "assigned")
    assert assigned["actor"] is None
    details = {"source": "routing", "decision_id": str(decision.id), "rule": "Everything"}
    assert assigned["data"] == {
        "from": None,
        "to": {"id": ws.agent.id, "name": "Abe Agent"},
        "automatic": True,
        **details,
    }
    team_event = next(e for e in staff_events if e["type"] == "team_changed")
    assert team_event["data"]["source"] == "routing"
    # Customers see the status change as automatic, and nothing about routing.
    customer_events = (
        await api.get(
            f"{API}/tickets/{ticket['id']}/events", headers=in_org(ws.customer, ws.org_id)
        )
    ).json()
    assert customer_events[-1]["data"] == {"from": "open", "to": "assigned", "automatic": True}

    [record] = (await history(api, ws, ticket)).json()
    assert (record["trigger"], record["applied"], record["actor"]) == ("created", True, None)
    assert record["rule"] == {"id": record["rule"]["id"], "name": "Everything"}
    assert [(c["name"], c["rank"], c["active_tickets"]) for c in record["candidates"]] == [
        ("Abe Agent", 1, 0),
        ("Lena Lead", 2, 1),
    ]
    assert record["explanation"] == (
        "Rule “Everything” matched (matches every ticket) and sent the ticket to the Support "
        "team. Assigned to Abe Agent, the least loaded available agent (0 of 10 active tickets)."
    )
    assert f"ticket_assigned: #{ticket['number']} was assigned to you" in await notices(
        api, ws, ws.agent
    )
    updated = [e for e in broker.published if e.type == "ticket.updated"]
    assert [(e.ticket_id, e.staff_only) for e in updated] == [(ticket["id"], False)]

    # The job running again (a redelivery) changes nothing.
    assert await router.new(ticket) is None
    assert len((await history(api, ws, ticket)).json()) == 1

    # Both now have one ticket: Lena waited longer (never assigned automatically).
    second = await create_ticket(api, ws.customer, ws.org_id)
    assert (await router.new(second)).assignee_name == "Lena Lead"


async def test_round_robin_and_skill_match(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    await set_mode(api, ws, "on")
    await router.online(ws.agent, crew.lead)
    admin = in_org(ws.admin, ws.org_id)
    rule = await add_rule(
        api,
        ws,
        "Refunds",
        crew.team["id"],
        strategy="skill_match",
        required_skills=[{"skill": "refunds", "level": 2}],
        conditions={"all": [condition("subject", "contains", "refund")]},
    )
    await add_rule(api, ws, "Rest", crew.team["id"], strategy="round_robin")
    for user, level in ((ws.agent, 2), (crew.lead, 5)):
        await api.put(
            f"{API}/agents/{user.id}/profile",
            json={"skills": [{"skill": "refunds", "level": level}]},
            headers=admin,
        )

    refunds = [
        await create_ticket(api, ws.customer, ws.org_id, subject=f"Refund {i}") for i in (1, 2)
    ]
    # The expert gets both, though she's busier after the first.
    assert [(await router.new(t)).assignee_name for t in refunds] == ["Lena Lead", "Lena Lead"]

    # Round robin alternates, newcomers to automatic assignment first.
    others = [
        await create_ticket(api, ws.customer, ws.org_id, subject=f"Other {i}") for i in range(3)
    ]
    names = [(await router.new(t)).assignee_name for t in others]
    assert names == ["Abe Agent", "Lena Lead", "Abe Agent"]

    # Capacity 0: no automatic assignments for Lena anymore.
    await api.put(
        f"{API}/agents/{crew.lead.id}/profile", json={"max_active_tickets": 0}, headers=admin
    )
    await api.patch(
        f"{API}/routing/rules/{rule['id']}", json={"required_skills": []}, headers=admin
    )
    third = await create_ticket(api, ws.customer, ws.org_id, subject="Refund 3")
    assert (await router.new(third)).assignee_name == "Abe Agent"


async def test_when_nobody_can_take_it_the_ticket_waits_and_leads_are_told(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    await set_mode(api, ws, "on")
    await add_rule(
        api,
        ws,
        "Refunds",
        crew.team["id"],
        required_skills=[{"skill": "refunds", "level": 3}],
    )
    await router.online(ws.agent)  # Lena is offline
    ticket = await create_ticket(api, ws.customer, ws.org_id, subject="Money back")

    decision = await router.new(ticket)
    assert decision.outcome == "no_agent"
    waiting = await fetch(api, ws, ticket)
    assert (waiting["assignee"], waiting["status"], waiting["team"]["name"]) == (
        None,
        "open",
        "Support",
    )
    assert decision.explanation.endswith(
        "Nobody there can take it now: Abe Agent (no refunds skill, needs 3), "
        "Lena Lead (offline; no refunds skill, needs 3). It stays unassigned."
    )
    assert f"routing_no_agent: Nobody in Support can take #{ticket['number']}" in await notices(
        api, ws, crew.lead
    )
    assert not [n for n in await notices(api, ws, ws.admin) if n.startswith("routing")]

    # Abe learns refunds; an agent reroutes, and it's theirs.
    await api.put(
        f"{API}/agents/{ws.agent.id}/profile",
        json={"skills": [{"skill": "refunds", "level": 4}]},
        headers=in_org(ws.admin, ws.org_id),
    )
    rerouted = await api.post(
        f"{API}/tickets/{ticket['id']}/reroute", headers=in_org(ws.agent, ws.org_id)
    )
    assert rerouted.status_code == 200, rerouted.text
    body = rerouted.json()
    assert (body["trigger"], body["outcome"], body["actor"]["full_name"]) == (
        "reroute",
        "assigned",
        "Abe Agent",
    )
    assert (await fetch(api, ws, ticket))["assignee"]["full_name"] == "Abe Agent"
    assert [d["trigger"] for d in (await history(api, ws, ticket)).json()] == [
        "reroute",
        "created",
    ]

    again = await api.post(
        f"{API}/tickets/{ticket['id']}/reroute", headers=in_org(ws.agent, ws.org_id)
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "not_routable"


async def test_manual_teams_unmatched_tickets_and_shadow_mode(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    await router.online(ws.agent, crew.lead)
    await add_rule(
        api,
        ws,
        "VIP",
        crew.team["id"],
        strategy="manual",
        conditions={"all": [condition("customer.plan", "eq", "gold")]},
    )
    staff = in_org(ws.agent, ws.org_id)
    gold = (
        await api.post(
            f"{API}/customers",
            json={"name": "Goldie", "metadata": {"plan": "Gold"}},
            headers=staff,
        )
    ).json()
    await set_mode(api, ws, "on")

    vip = await create_ticket(api, ws.agent, ws.org_id, customer_id=gold["id"])
    decision = await router.new(vip)
    assert decision.outcome == "team_only"
    routed = await fetch(api, ws, vip)
    assert (routed["team"]["name"], routed["assignee"]) == ("Support", None)

    plain = await create_ticket(api, ws.customer, ws.org_id)
    decision = await router.new(plain)
    assert decision.outcome == "no_rule"
    assert decision.explanation == (
        "The only active routing rule, “VIP”, didn't match (not customer.plan is gold), so "
        "the ticket stays in the unassigned queue."
    )
    assert (await fetch(api, ws, plain))["team"] is None

    # Shadow mode: decided and recorded, nothing changed, nobody told.
    await set_mode(api, ws, "shadow")
    shadow = await create_ticket(api, ws.agent, ws.org_id, customer_id=gold["id"])
    decision = await router.new(shadow)
    assert (decision.outcome, decision.applied) == ("team_only", False)
    untouched = await fetch(api, ws, shadow)
    assert (untouched["team"], untouched["assignee"], untouched["status"]) == (None, None, "open")
    [record] = (await history(api, ws, shadow)).json()
    assert record["applied"] is False


async def test_a_persons_choice_always_wins(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    await set_mode(api, ws, "on")
    await router.online(ws.agent, crew.lead)
    await add_rule(api, ws, "Everything", crew.team["id"])
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    # Someone assigns it before the job runs.
    await api.patch(
        f"{API}/tickets/{ticket['id']}",
        json={"assignee_id": crew.lead.id},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert await router.new(ticket) is None
    assert (await fetch(api, ws, ticket))["assignee"]["full_name"] == "Lena Lead"
    assert (await history(api, ws, ticket)).json() == []


async def test_a_team_chosen_by_a_person_is_kept(
    api: httpx2.AsyncClient,
    ws: Workspace,
    crew: Crew,
    router: Router,
    routing_queue: RecordingRoutingQueue,
    db_session: AsyncSession,
) -> None:
    await set_mode(api, ws, "on")
    await router.online(ws.agent, crew.lead)
    other = await add_team(api, ws, "Billing", [{"user_id": ws.agent.id}])
    await add_rule(api, ws, "Everything", crew.team["id"])
    customer = (await create_ticket(api, ws.customer, ws.org_id))["customer"]
    chosen = await create_ticket(
        api, ws.agent, ws.org_id, customer_id=customer["id"], team_id=other["id"]
    )
    assert uuid.UUID(chosen["id"]) not in routing_queue.ids
    assert await router.new(chosen) is None  # even if a job runs anyway
    later = utcnow() + timedelta(minutes=3)
    await sweep_unrouted(db_session, presence=router.presence, now=later)
    kept = await fetch(api, ws, chosen)
    assert (kept["team"]["name"], kept["assignee"]) == ("Billing", None)
    assert (await history(api, ws, chosen)).json() == []

    # Asking for routing explicitly applies the rules.
    rerouted = await api.post(
        f"{API}/tickets/{chosen['id']}/reroute", headers=in_org(ws.agent, ws.org_id)
    )
    assert rerouted.json()["team"]["name"] == "Support"


async def test_reroute_rules(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    # Routing is off, but a person can still ask for it.
    await router.online(ws.agent)
    await add_rule(api, ws, "Everything", crew.team["id"])
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    url = f"{API}/tickets/{ticket['id']}/reroute"

    assert (await api.post(url, headers=in_org(ws.customer, ws.org_id))).status_code == 403
    missing = f"{API}/tickets/{uuid.uuid4()}/reroute"
    assert (await api.post(missing, headers=in_org(ws.agent, ws.org_id))).status_code == 404
    closed = await create_ticket(api, ws.customer, ws.org_id)
    await api.post(f"{API}/tickets/{closed['id']}/close", headers=in_org(ws.agent, ws.org_id))
    refused = await api.post(
        f"{API}/tickets/{closed['id']}/reroute", headers=in_org(ws.agent, ws.org_id)
    )
    assert refused.status_code == 409

    done = await api.post(url, headers=in_org(ws.admin, ws.org_id))
    assert done.json()["outcome"] == "assigned"
    assert (await history(api, ws, ticket, ws.customer)).status_code == 403


# --- Simulator ------------------------------------------------------------------------------


async def test_the_simulator_explains_without_changing_anything(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router
) -> None:
    await router.online(ws.agent, crew.lead)
    await add_category(api, ws, "Billing")
    await add_rule(
        api,
        ws,
        "Urgent billing",
        crew.team["id"],
        conditions={
            "all": [condition("category", "eq", "billing")],
            "any": [
                condition("priority", "eq", "urgent"),
                condition("customer.plan", "eq", "gold"),
            ],
        },
    )
    admin = in_org(ws.admin, ws.org_id)
    hypothetical = await api.post(
        f"{API}/routing/simulate",
        json={"category": "billing", "priority": "normal", "customer": {"plan": "gold"}},
        headers=admin,
    )
    assert hypothetical.status_code == 200, hypothetical.text
    result = hypothetical.json()
    assert (result["outcome"], result["rule"]["name"], result["team"]["name"]) == (
        "assigned",
        "Urgent billing",
        "Support",
    )
    assert result["rules"][0]["detail"] == "category is billing, customer.plan is gold"
    assert result["assignee"]["rank"] == 1
    assert "Would assign to" in result["explanation"]

    ticket = await create_ticket(api, ws.customer, ws.org_id)
    existing = (
        await api.post(f"{API}/routing/simulate", json={"ticket_id": ticket["id"]}, headers=admin)
    ).json()
    assert existing["outcome"] == "no_rule"
    assert existing["rules"][0]["detail"] == "not category is billing"
    # Nothing was written.
    assert (await history(api, ws, ticket)).json() == []
    assert (await fetch(api, ws, ticket))["assignee"] is None

    both = await api.post(
        f"{API}/routing/simulate",
        json={"ticket_id": ticket["id"], "priority": "urgent"},
        headers=admin,
    )
    assert both.status_code == 422
    agent = await api.post(f"{API}/routing/simulate", json={}, headers=in_org(ws.agent, ws.org_id))
    assert agent.status_code == 403


# --- The sweep ------------------------------------------------------------------------------


async def test_the_sweep_routes_tickets_whose_job_never_ran(
    api: httpx2.AsyncClient,
    ws: Workspace,
    crew: Crew,
    router: Router,
    db_session: AsyncSession,
) -> None:
    await router.online(ws.agent, crew.lead)
    await add_rule(api, ws, "Everything", crew.team["id"])
    off = await create_ticket(api, ws.customer, ws.org_id, subject="Before routing was on")
    await set_mode(api, ws, "on")
    lost = await create_ticket(api, ws.customer, ws.org_id, subject="Job lost")
    routed = await create_ticket(api, ws.customer, ws.org_id, subject="Job ran")
    await router.new(routed)

    async def sweep(**kwargs: Any) -> int:
        return await sweep_unrouted(db_session, presence=router.presence, **kwargs)

    assert await sweep() == 0  # too recent: the job may still be on its way
    later = utcnow() + timedelta(minutes=3)
    # Both unrouted tickets qualify by age; "off" was opened while routing was off, but
    # it's still open, unassigned and less than a day old.
    assert await sweep(now=later) == 2
    for ticket in (lost, off):
        [record] = (await history(api, ws, ticket)).json()
        assert record["trigger"] == "sweep"
    assert await sweep(now=later) == 0

    # Tickets older than a day are left for people.
    old = await create_ticket(api, ws.customer, ws.org_id, subject="Ancient")
    assert await sweep(now=utcnow() + timedelta(days=2)) == 0
    assert (await history(api, ws, old)).json() == []

    await set_mode(api, ws, "off")
    assert await sweep(now=later) == 0


# --- Database -------------------------------------------------------------------------------


async def test_routing_tables_are_staff_only(
    api: httpx2.AsyncClient, ws: Workspace, crew: Crew, router: Router, db_session: AsyncSession
) -> None:
    await router.online(ws.agent)
    await add_rule(api, ws, "Everything", crew.team["id"])
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    await api.post(f"{API}/tickets/{ticket['id']}/reroute", headers=in_org(ws.agent, ws.org_id))

    async def visible(user: AuthedUser) -> list[int]:
        await set_rls_context(
            db_session,
            organization_id=uuid.UUID(ws.org_id),
            user_id=uuid.UUID(user.id),
            bypass=False,
        )
        counts = []
        for table in ("routing_rules", "routing_decisions"):
            counts.append(
                (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            )
        return counts

    assert await visible(ws.agent) == [1, 1]
    assert await visible(ws.customer) == [0, 0]


# --- Concurrency (real transactions) ---------------------------------------------------------


@dataclass
class Committed:
    org_id: uuid.UUID
    agent_id: uuid.UUID
    tickets: list[uuid.UUID]


@pytest.fixture
async def committed(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[Committed]:
    """An organization committed for real (each routing run needs its own connection):
    one agent with room for one ticket, a catch-all rule, and two open tickets."""
    org_id, agent_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    team_id, customer_id = uuid.uuid4(), uuid.uuid4()
    tickets = [uuid.uuid4(), uuid.uuid4()]
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        for user_id in (agent_id, owner_id):
            session.add(
                User(id=user_id, email=f"{user_id}@example.com", password_hash="x", full_name="Ana")
            )
        session.add(
            Organization(
                id=org_id,
                name="Race Co",
                slug=f"race-{org_id.hex[:8]}",
                settings={"routing_mode": "on"},
            )
        )
        await session.flush()
        session.add(
            OrganizationMember(organization_id=org_id, user_id=agent_id, role=OrgRole.AGENT)
        )
        session.add(
            Team(id=team_id, organization_id=org_id, name="All", description="", is_active=True)
        )
        session.add(Customer(id=customer_id, organization_id=org_id, name="Cy"))
        await session.flush()
        session.add(
            TeamMember(
                organization_id=org_id, team_id=team_id, user_id=agent_id, role=TeamRole.MEMBER
            )
        )
        session.add(AgentProfile(organization_id=org_id, user_id=agent_id, max_active_tickets=1))
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
        for number, ticket_id in enumerate(tickets, 1):
            session.add(
                Ticket(
                    id=ticket_id,
                    organization_id=org_id,
                    number=number,
                    customer_id=customer_id,
                    subject=f"Race {number}",
                    status=TicketStatus.OPEN,
                    priority=TicketPriority.NORMAL,
                )
            )
        await session.commit()
    try:
        yield Committed(org_id, agent_id, tickets)
    finally:
        async with session_factory() as session:
            await set_rls_context(session, bypass=True)
            await session.execute(delete(Ticket).where(Ticket.organization_id == org_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.execute(delete(User).where(User.id.in_([agent_id, owner_id])))
            await session.commit()


async def _route_concurrently(
    session_factory: async_sessionmaker[AsyncSession],
    committed: Committed,
    ticket_ids: list[uuid.UUID],
) -> list[Any]:
    presence = InMemoryPresence()
    await presence.touch(committed.org_id, committed.agent_id)

    async def run(ticket_id: uuid.UUID) -> Any:
        async with session_factory() as session:
            await set_rls_context(session, bypass=True)
            return await RoutingService(session, presence).route(
                committed.org_id, ticket_id, trigger=RoutingTrigger.CREATED
            )

    return await asyncio.gather(*(run(t) for t in ticket_ids))


async def test_concurrent_runs_never_exceed_capacity(
    session_factory: async_sessionmaker[AsyncSession], committed: Committed
) -> None:
    decisions = await _route_concurrently(session_factory, committed, committed.tickets)
    assert sorted(d.outcome for d in decisions) == ["assigned", "no_agent"]
    loser = next(d for d in decisions if d.outcome == "no_agent")
    assert "at capacity, 1 of 1" in loser.explanation


async def test_two_runs_for_one_ticket_route_it_once(
    session_factory: async_sessionmaker[AsyncSession], committed: Committed
) -> None:
    ticket = committed.tickets[0]
    decisions = await _route_concurrently(session_factory, committed, [ticket, ticket])
    assert sorted(d is None for d in decisions) == [False, True]
    async with session_factory() as session:
        await set_rls_context(session, bypass=True)
        count = (
            await session.execute(
                text("SELECT count(*) FROM routing_decisions WHERE ticket_id = :id"), {"id": ticket}
            )
        ).scalar_one()
    assert count == 1


def test_the_scheduler_runs_the_sweep() -> None:
    from app.workers import tasks  # noqa: F401  (registers the tasks)
    from app.workers.celery_app import celery_app

    job = celery_app.conf.beat_schedule["routing-sweep"]
    assert job["task"] == "routing.sweep" and job["task"] in celery_app.tasks
    assert "routing.route_ticket" in celery_app.tasks
