"""Teams and agents of the organization in ``X-Organization-Id`` (Module 10, phase 5).
Staff read them; admins manage teams, capacity and skills; everyone on the staff sets
their own status."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, PresenceDep, TenantMembership
from app.schemas.common import error_responses
from app.schemas.teams import (
    AgentProfileUpdate,
    AgentRead,
    PresenceUpdate,
    TeamCreate,
    TeamMembersUpdate,
    TeamRead,
    TeamUpdate,
)
from app.services.teams import AgentService, TeamService

router = APIRouter(
    prefix="/teams", tags=["teams"], responses=error_responses(401, 403, 404, 409, 422)
)
agents_router = APIRouter(
    prefix="/agents", tags=["teams"], responses=error_responses(401, 403, 404, 422)
)


@router.get("", summary="List teams with their members")
async def list_teams(
    member: TenantMembership,
    session: DbSession,
    include_inactive: Annotated[bool, Query(description="Also deactivated ones.")] = False,
) -> list[TeamRead]:
    teams = await TeamService(session).list_teams(member, include_inactive=include_inactive)
    return [TeamRead.build(t) for t in teams]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Add a team (admin)")
async def create_team(body: TeamCreate, member: TenantMembership, session: DbSession) -> TeamRead:
    """409 `team_exists` if the name is taken in this organization (ignoring case)."""
    return TeamRead.build(await TeamService(session).create(member, body))


@router.patch("/{team_id}", summary="Rename, describe or (de)activate a team (admin)")
async def update_team(
    team_id: uuid.UUID, body: TeamUpdate, member: TenantMembership, session: DbSession
) -> TeamRead:
    return TeamRead.build(await TeamService(session).update(member, team_id, body))


@router.put("/{team_id}/members", summary="Set a team's members and leads (admin)")
async def set_team_members(
    team_id: uuid.UUID, body: TeamMembersUpdate, member: TenantMembership, session: DbSession
) -> TeamRead:
    """Replaces the whole membership. 400 `invalid_team_member` if someone isn't an
    admin or agent of the organization."""
    return TeamRead.build(await TeamService(session).set_members(member, team_id, body))


@agents_router.get("", summary="List admins and agents with status, workload and skills")
async def list_agents(
    member: TenantMembership, session: DbSession, presence: PresenceDep
) -> list[AgentRead]:
    return await AgentService(session, presence).list_agents(member)


@agents_router.get("/me", summary="Your own status, workload and skills")
async def get_me(member: TenantMembership, session: DbSession, presence: PresenceDep) -> AgentRead:
    return await AgentService(session, presence).get_agent(member, member.user_id)


@agents_router.put("/me/presence", summary="Set your status: available, busy or away")
async def set_presence(
    body: PresenceUpdate, member: TenantMembership, session: DbSession, presence: PresenceDep
) -> AgentRead:
    """Routing only picks agents who are available and have the app open."""
    return await AgentService(session, presence).set_presence(member, body.status)


@agents_router.put("/{user_id}/profile", summary="Set an agent's capacity and skills (admin)")
async def update_agent_profile(
    user_id: uuid.UUID,
    body: AgentProfileUpdate,
    member: TenantMembership,
    session: DbSession,
    presence: PresenceDep,
) -> AgentRead:
    """404 `agent_not_found` if the user isn't an admin or agent of the organization."""
    return await AgentService(session, presence).update_profile(member, user_id, body)
