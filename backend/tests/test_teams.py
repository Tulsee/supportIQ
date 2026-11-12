"""Teams and agents (Module 10, phase 5): teams with members and leads, capacity and
skills, status and presence, a team on each ticket, cleanup when someone leaves the
staff, and staff-only access in the database."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_rls_context
from app.core.security import utcnow
from app.websocket import presence as presence_module
from app.websocket.presence import InMemoryPresence
from tests.factories import (
    API,
    AuthedUser,
    Outbox,
    Workspace,
    add_member,
    create_org,
    create_ticket,
    create_user,
    in_org,
)
from tests.test_realtime import stream

pytestmark = pytest.mark.anyio

STAFF_TABLES = ("teams", "team_members", "agent_profiles", "agent_skills")


async def add_team(
    api: httpx2.AsyncClient, ws: Workspace, name: str, members: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/teams", json={"name": name}, headers=in_org(ws.admin, ws.org_id)
    )
    assert response.status_code == 201, response.text
    team = response.json()
    if members is not None:
        team = await set_members(api, ws, team["id"], members)
    return team


async def set_members(
    api: httpx2.AsyncClient, ws: Workspace, team_id: str, members: list[dict[str, Any]]
) -> dict[str, Any]:
    response = await api.put(
        f"{API}/teams/{team_id}/members",
        json={"members": members},
        headers=in_org(ws.admin, ws.org_id),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def agents(api: httpx2.AsyncClient, ws: Workspace) -> dict[str, dict[str, Any]]:
    response = await api.get(f"{API}/agents", headers=in_org(ws.agent, ws.org_id))
    assert response.status_code == 200, response.text
    return {a["user_id"]: a for a in response.json()}


async def audit_actions(api: httpx2.AsyncClient, ws: Workspace) -> list[dict[str, Any]]:
    response = await api.get(f"{API}/audit-log", headers=in_org(ws.admin, ws.org_id))
    return [e for e in response.json()["items"] if e["action"].startswith(("team.", "agent."))]


# --- Teams ---------------------------------------------------------------------------------


async def test_admins_manage_teams_members_and_leads(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    admin = in_org(ws.admin, ws.org_id)
    team = await add_team(api, ws, "Billing")
    assert (team["name"], team["is_active"], team["members"]) == ("Billing", True, [])

    duplicate = await api.post(f"{API}/teams", json={"name": "  billing "}, headers=admin)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "team_exists"

    team = await set_members(
        api,
        ws,
        team["id"],
        [{"user_id": ws.agent.id}, {"user_id": ws.admin.id, "role": "lead"}],
    )
    # Leads first, then by name.
    assert [(m["full_name"], m["role"]) for m in team["members"]] == [
        ("Ada Admin", "lead"),
        ("Abe Agent", "member"),
    ]

    # Only admins and agents of the organization can be members.
    for user_id in (ws.customer.id, str(uuid.uuid4())):
        rejected = await api.put(
            f"{API}/teams/{team['id']}/members",
            json={"members": [{"user_id": user_id}]},
            headers=admin,
        )
        assert rejected.status_code == 400, user_id
        assert rejected.json()["error"]["code"] == "invalid_team_member"
    twice = await api.put(
        f"{API}/teams/{team['id']}/members",
        json={"members": [{"user_id": ws.agent.id}, {"user_id": ws.agent.id, "role": "lead"}]},
        headers=admin,
    )
    assert twice.status_code == 422

    # The agent becomes the lead and the admin leaves.
    team = await set_members(api, ws, team["id"], [{"user_id": ws.agent.id, "role": "lead"}])
    assert [(m["full_name"], m["role"]) for m in team["members"]] == [("Abe Agent", "lead")]

    renamed = await api.patch(
        f"{API}/teams/{team['id']}", json={"name": "Billing & refunds"}, headers=admin
    )
    assert renamed.json()["name"] == "Billing & refunds"
    off = await api.patch(f"{API}/teams/{team['id']}", json={"is_active": False}, headers=admin)
    assert off.json()["is_active"] is False
    assert off.json()["members"] == team["members"]  # kept for reactivation

    listed = await api.get(f"{API}/teams", headers=in_org(ws.agent, ws.org_id))
    assert listed.json() == []
    listed = await api.get(
        f"{API}/teams", params={"include_inactive": True}, headers=in_org(ws.agent, ws.org_id)
    )
    assert [t["name"] for t in listed.json()] == ["Billing & refunds"]

    entries = await audit_actions(api, ws)
    assert [e["action"] for e in reversed(entries)] == [
        "team.created",
        "team.members_changed",
        "team.members_changed",
        "team.updated",
        "team.updated",
    ]
    second_change = entries[-3]["changes"]
    assert second_change == {
        "removed": ["Ada Admin"],
        "role": ["Abe Agent: member → lead"],
    }
    assert entries[0]["summary"] == "Deactivated the team Billing & refunds"


async def test_only_admins_change_teams_and_customers_see_none(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    team = await add_team(api, ws, "Support")
    agent = in_org(ws.agent, ws.org_id)
    assert (await api.post(f"{API}/teams", json={"name": "Mine"}, headers=agent)).status_code == 403
    assert (
        await api.patch(f"{API}/teams/{team['id']}", json={"name": "Xyz"}, headers=agent)
    ).status_code == 403
    assert (
        await api.put(f"{API}/teams/{team['id']}/members", json={"members": []}, headers=agent)
    ).status_code == 403
    customer = in_org(ws.customer, ws.org_id)
    assert (await api.get(f"{API}/teams", headers=customer)).status_code == 403
    assert (await api.get(f"{API}/agents", headers=customer)).status_code == 403

    # Another organization's admin can't touch it.
    outsider = await create_user(api)
    other = await create_org(api, outsider, "Other Co")
    missing = await api.patch(
        f"{API}/teams/{team['id']}", json={"name": "Mine"}, headers=in_org(outsider, other["id"])
    )
    assert missing.status_code == 404


# --- Agents --------------------------------------------------------------------------------


async def test_the_roster_shows_status_workload_skills_and_teams(
    api: httpx2.AsyncClient, ws: Workspace, presence: InMemoryPresence
) -> None:
    roster = await agents(api, ws)
    assert set(roster) == {ws.admin.id, ws.agent.id}  # staff only
    agent = roster[ws.agent.id]
    assert {k: agent[k] for k in ("status", "presence", "online", "max_active_tickets")} == {
        "status": "available",
        "presence": "offline",  # no open connection
        "online": False,
        "max_active_tickets": 10,
    }
    assert (agent["active_tickets"], agent["skills"], agent["teams"]) == (0, [], [])

    # Workload: assigned and in progress count, waiting for the customer doesn't.
    staff = in_org(ws.agent, ws.org_id)
    for status in (None, "waiting_for_customer", "in_progress"):
        ticket = await create_ticket(api, ws.customer, ws.org_id)
        changes: list[dict[str, Any]] = [{"assignee_id": ws.agent.id}]
        if status == "waiting_for_customer":
            changes += [{"status": "in_progress"}, {"status": status}]
        elif status:
            changes.append({"status": status})
        for change in changes:
            response = await api.patch(f"{API}/tickets/{ticket['id']}", json=change, headers=staff)
            assert response.status_code == 200, response.text
    team = await add_team(api, ws, "Billing", [{"user_id": ws.agent.id, "role": "lead"}])
    await add_team(api, ws, "Returns", [{"user_id": ws.agent.id}])
    retired = await add_team(api, ws, "Retired", [{"user_id": ws.agent.id}])
    await api.patch(
        f"{API}/teams/{retired['id']}",
        json={"is_active": False},
        headers=in_org(ws.admin, ws.org_id),
    )

    await presence.touch(uuid.UUID(ws.org_id), uuid.UUID(ws.agent.id))
    agent = (await agents(api, ws))[ws.agent.id]
    assert (agent["presence"], agent["online"], agent["active_tickets"]) == ("available", True, 2)
    assert agent["teams"] == [
        {"id": team["id"], "name": "Billing", "role": "lead"},
        {"id": agent["teams"][1]["id"], "name": "Returns", "role": "member"},
    ]


async def test_admins_set_capacity_and_skills(api: httpx2.AsyncClient, ws: Workspace) -> None:
    admin = in_org(ws.admin, ws.org_id)
    url = f"{API}/agents/{ws.agent.id}/profile"
    response = await api.put(
        url,
        json={
            "max_active_tickets": 3,
            "skills": [{"skill": " Refunds ", "level": 4}, {"skill": "billing", "level": 2}],
        },
        headers=admin,
    )
    assert response.status_code == 200, response.text
    agent = response.json()
    assert agent["max_active_tickets"] == 3
    assert agent["skills"] == [{"skill": "billing", "level": 2}, {"skill": "refunds", "level": 4}]

    # Skills are replaced as a whole; capacity stays when not given.
    response = await api.put(
        url, json={"skills": [{"skill": "refunds", "level": 5}]}, headers=admin
    )
    agent = response.json()
    assert (agent["max_active_tickets"], agent["skills"]) == (3, [{"skill": "refunds", "level": 5}])
    assert (await agents(api, ws))[ws.agent.id]["skills"] == agent["skills"]

    entries = await audit_actions(api, ws)
    assert [e["action"] for e in entries] == ["agent.profile_updated"] * 2
    assert entries[0]["changes"] == {
        "skills": {"from": ["billing:2", "refunds:4"], "to": ["refunds:5"]}
    }
    assert entries[1]["changes"]["max_active_tickets"] == {"from": 10, "to": 3}

    # Nothing changed: no audit entry.
    await api.put(url, json={"max_active_tickets": 3}, headers=admin)
    assert len(await audit_actions(api, ws)) == 2

    for body in (
        {"skills": [{"skill": "refunds", "level": 6}]},
        {"skills": [{"skill": "refunds", "level": 1}, {"skill": "REFUNDS", "level": 2}]},
        {"skills": [{"skill": "no spaces", "level": 1}]},
        {"max_active_tickets": -1},
    ):
        assert (await api.put(url, json=body, headers=admin)).status_code == 422, body
    not_staff = await api.put(
        f"{API}/agents/{ws.customer.id}/profile", json={"max_active_tickets": 1}, headers=admin
    )
    assert not_staff.status_code == 404
    assert not_staff.json()["error"]["code"] == "agent_not_found"
    by_agent = await api.put(
        url, json={"max_active_tickets": 50}, headers=in_org(ws.agent, ws.org_id)
    )
    assert by_agent.status_code == 403


async def test_staff_set_their_own_status(api: httpx2.AsyncClient, ws: Workspace) -> None:
    staff = in_org(ws.agent, ws.org_id)
    me = (await api.get(f"{API}/agents/me", headers=staff)).json()
    assert (me["user_id"], me["status"], me["status_changed_at"]) == (
        ws.agent.id,
        "available",
        None,
    )

    busy = await api.put(f"{API}/agents/me/presence", json={"status": "busy"}, headers=staff)
    assert busy.status_code == 200, busy.text
    assert busy.json()["status"] == "busy" and busy.json()["status_changed_at"] is not None
    assert busy.json()["presence"] == "offline"  # still not connected
    assert (await agents(api, ws))[ws.agent.id]["status"] == "busy"
    assert (await agents(api, ws))[ws.admin.id]["status"] == "available"

    invalid = await api.put(f"{API}/agents/me/presence", json={"status": "offline"}, headers=staff)
    assert invalid.status_code == 422  # offline means "app closed", not a choice
    customer = in_org(ws.customer, ws.org_id)
    assert (
        await api.put(f"{API}/agents/me/presence", json={"status": "away"}, headers=customer)
    ).status_code == 403
    assert (await api.get(f"{API}/agents/me", headers=customer)).status_code == 403


async def test_presence_follows_the_open_connection(
    api: httpx2.AsyncClient,
    ws: Workspace,
    presence: InMemoryPresence,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = utcnow()
    presence.clock = lambda: now
    monkeypatch.setattr(presence_module, "REFRESH_SECONDS", 0.05)
    await api.put(
        f"{API}/agents/me/presence", json={"status": "away"}, headers=in_org(ws.agent, ws.org_id)
    )

    async with stream(ws.agent, ws.org_id), stream(ws.customer, ws.org_id):
        agent = (await agents(api, ws))[ws.agent.id]
        assert (agent["online"], agent["presence"]) == (True, "away")
        assert set(presence.seen[uuid.UUID(ws.org_id)]) == {uuid.UUID(ws.agent.id)}

        # Two minutes pass: the open socket keeps refreshing.
        now += timedelta(minutes=2)
        await asyncio.sleep(0.2)
        assert (await agents(api, ws))[ws.agent.id]["online"] is True

    # The tab closes; after the TTL they're offline without anything being sent.
    now += timedelta(seconds=presence_module.TTL_SECONDS + 1)
    agent = (await agents(api, ws))[ws.agent.id]
    assert (agent["online"], agent["presence"], agent["last_seen_at"]) == (False, "offline", None)


# --- Tickets -------------------------------------------------------------------------------


async def test_tickets_get_a_team_that_customers_never_see(
    api: httpx2.AsyncClient, ws: Workspace
) -> None:
    team = await add_team(api, ws, "Billing")
    staff = in_org(ws.agent, ws.org_id)
    ticket = await create_ticket(api, ws.customer, ws.org_id)
    other = await create_ticket(api, ws.agent, ws.org_id, customer_id=ticket["customer"]["id"])

    response = await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"team_id": team["id"]}, headers=staff
    )
    assert response.status_code == 200, response.text
    assert response.json()["team"] == {"id": team["id"], "name": "Billing"}
    events = (await api.get(f"{API}/tickets/{ticket['id']}/events", headers=staff)).json()
    changed = [e for e in events if e["type"] == "team_changed"]
    assert [e["data"] for e in changed] == [
        {"from": None, "to": {"id": team["id"], "name": "Billing"}}
    ]

    listed = await api.get(f"{API}/tickets", params={"team_id": team["id"]}, headers=staff)
    assert [t["id"] for t in listed.json()["items"]] == [ticket["id"]]
    assert other["team"] is None

    customer = in_org(ws.customer, ws.org_id)
    seen = (await api.get(f"{API}/tickets/{ticket['id']}", headers=customer)).json()
    assert seen["team"] is None
    events = (await api.get(f"{API}/tickets/{ticket['id']}/events", headers=customer)).json()
    assert "team_changed" not in {e["type"] for e in events}
    # The filter is ignored for customers (it would reveal how their tickets are handled).
    mine = await api.get(f"{API}/tickets", params={"team_id": str(uuid.uuid4())}, headers=customer)
    assert {t["id"] for t in mine.json()["items"]} == {ticket["id"], other["id"]}

    cleared = await api.patch(
        f"{API}/tickets/{ticket['id']}", json={"team_id": None}, headers=staff
    )
    assert cleared.json()["team"] is None

    await api.patch(
        f"{API}/teams/{team['id']}", json={"is_active": False}, headers=in_org(ws.admin, ws.org_id)
    )
    for team_id in (team["id"], str(uuid.uuid4())):
        rejected = await api.patch(
            f"{API}/tickets/{ticket['id']}", json={"team_id": team_id}, headers=staff
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "invalid_team"
    as_customer = await api.post(
        f"{API}/tickets",
        json={"subject": "Hi", "message": "Help", "team_id": team["id"]},
        headers=customer,
    )
    assert as_customer.status_code == 403


async def test_leaving_the_staff_clears_teams_skills_and_profile(
    api: httpx2.AsyncClient, ws: Workspace, outbox: Outbox
) -> None:
    second: AuthedUser = await create_user(api, full_name="Sam Second")
    second_member = await add_member(api, outbox, ws.admin, ws.org_id, second, "agent")
    members = (
        await api.get(
            f"{API}/organizations/{ws.org_id}/members",
            params={"limit": 100},
            headers=ws.admin.headers,
        )
    ).json()["items"]
    agent_member = next(m for m in members if m["user_id"] == ws.agent.id)
    team = await add_team(
        api, ws, "Billing", [{"user_id": ws.agent.id, "role": "lead"}, {"user_id": second.id}]
    )
    admin = in_org(ws.admin, ws.org_id)
    for user in (ws.agent, second):
        await api.put(
            f"{API}/agents/{user.id}/profile",
            json={"skills": [{"skill": "refunds", "level": 3}]},
            headers=admin,
        )

    demoted = await api.patch(
        f"{API}/organizations/{ws.org_id}/members/{agent_member['id']}",
        json={"role": "customer"},
        headers=ws.admin.headers,
    )
    assert demoted.status_code == 200, demoted.text
    removed = await api.delete(
        f"{API}/organizations/{ws.org_id}/members/{second_member['id']}", headers=ws.admin.headers
    )
    assert removed.status_code == 204, removed.text

    team = (await api.get(f"{API}/teams", headers=admin)).json()[0]
    assert team["members"] == []
    roster = (await api.get(f"{API}/agents", headers=admin)).json()
    assert [a["user_id"] for a in roster] == [ws.admin.id]

    # Promoted back, they start from a clean profile.
    await api.patch(
        f"{API}/organizations/{ws.org_id}/members/{agent_member['id']}",
        json={"role": "agent"},
        headers=ws.admin.headers,
    )
    back = (await api.get(f"{API}/agents", headers=admin)).json()
    agent = next(a for a in back if a["user_id"] == ws.agent.id)
    assert (agent["skills"], agent["teams"]) == ([], [])


# --- Row-level security --------------------------------------------------------------------


async def test_team_and_agent_tables_are_staff_only_in_the_database(
    api: httpx2.AsyncClient, ws: Workspace, db_session: AsyncSession
) -> None:
    await add_team(api, ws, "Billing", [{"user_id": ws.agent.id, "role": "lead"}])
    admin = in_org(ws.admin, ws.org_id)
    await api.put(
        f"{API}/agents/{ws.agent.id}/profile",
        json={"skills": [{"skill": "refunds", "level": 3}]},
        headers=admin,
    )
    org = uuid.UUID(ws.org_id)

    async def visible(user: AuthedUser, organization_id: uuid.UUID) -> dict[str, int]:
        await set_rls_context(
            db_session, organization_id=organization_id, user_id=uuid.UUID(user.id), bypass=False
        )
        counts = {}
        for table in STAFF_TABLES:
            row = await db_session.execute(text(f"SELECT count(*) FROM {table}"))  # no filter
            counts[table] = row.scalar_one()
        return counts

    assert await visible(ws.agent, org) == dict.fromkeys(STAFF_TABLES, 1)
    assert await visible(ws.customer, org) == dict.fromkeys(STAFF_TABLES, 0)
    assert await visible(ws.agent, uuid.uuid4()) == dict.fromkeys(STAFF_TABLES, 0)


# --- Redis presence ------------------------------------------------------------------------


async def test_redis_presence_round_trip() -> None:
    from redis.asyncio import Redis

    from app.websocket.presence import RedisPresence

    client = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
    tracker = RedisPresence(client)
    org, other_org, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        await tracker.touch(org, user)
        assert set(await tracker.online(org)) == {user}
        assert await tracker.online(other_org) == {}
        # Someone last seen beyond the TTL is offline (and trimmed on the next write).
        stale = uuid.uuid4()
        key = f"{presence_module.KEY_PREFIX}{org}"
        await client.zadd(key, {str(stale): 1.0})
        assert stale not in await tracker.online(org)
        await tracker.touch(org, user)
        assert await client.zscore(key, str(stale)) is None
        assert 0 < await client.ttl(key) <= presence_module.TTL_SECONDS * 2
    finally:
        await client.delete(f"{presence_module.KEY_PREFIX}{org}")
        await client.aclose()


async def test_redis_presence_fails_open() -> None:
    from redis.asyncio import Redis

    from app.websocket.presence import RedisPresence

    client = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    try:
        tracker = RedisPresence(client)
        await tracker.touch(uuid.uuid4(), uuid.uuid4())  # no exception
        assert await tracker.online(uuid.uuid4()) == {}  # nobody, rather than an error
    finally:
        await client.aclose()
