"""Teams and agents (Module 10, phase 5).

Admins manage teams, their members and leads, and each agent's capacity and skills;
every change is audited. Each staff member sets their own status (available, busy,
away). Whether they're online comes from their open connection to the app
(``app.websocket.presence``). Routing (phase 6) reads all of it through
``AgentService.list_agents`` and ``active_ticket_counts``.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.security import utcnow
from app.models import (
    DEFAULT_MAX_ACTIVE_TICKETS,
    AgentProfile,
    AgentSkill,
    AgentStatus,
    OrganizationMember,
    OrgRole,
    Team,
    TeamMember,
    TeamRole,
    Ticket,
    TicketStatus,
    User,
)
from app.schemas.teams import (
    AgentProfileUpdate,
    AgentRead,
    AgentTeam,
    SkillLevel,
    TeamCreate,
    TeamMembersUpdate,
    TeamUpdate,
)
from app.services import audit
from app.services.authorization import STAFF_ROLES, require_role
from app.websocket.presence import PresenceTracker

# Tickets that count against an agent's capacity (decision 4): waiting for the
# customer doesn't.
CAPACITY_STATUSES = (TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)
TEAM_EXISTS = "A team with this name already exists"


async def active_ticket_counts(
    session: AsyncSession, organization_id: uuid.UUID, user_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, int]:
    """Tickets each agent is working on (assigned or in progress)."""
    query = (
        select(Ticket.assignee_id, func.count())
        .where(
            Ticket.organization_id == organization_id,
            Ticket.assignee_id.is_not(None),
            Ticket.status.in_(CAPACITY_STATUSES),
        )
        .group_by(Ticket.assignee_id)
    )
    if user_ids is not None:
        query = query.where(Ticket.assignee_id.in_(list(user_ids)))
    rows = await session.execute(query)
    return {user_id: count for user_id, count in rows.all()}


async def escalation_contacts(
    session: AsyncSession, organization_id: uuid.UUID, team_id: uuid.UUID | None
) -> list[uuid.UUID]:
    """Who hears about a ticket's SLA trouble beyond its assignee: the leads of its
    team, or the organization's admins if it has no (active) team or the team has
    no lead."""
    if team_id is not None:
        leads = await session.scalars(
            select(TeamMember.user_id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                Team.id == team_id,
                Team.organization_id == organization_id,
                Team.is_active.is_(True),
                TeamMember.role == TeamRole.LEAD,
            )
            .order_by(TeamMember.created_at)
        )
        if found := list(leads.all()):
            return found
    admins = await session.scalars(
        select(OrganizationMember.user_id).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OrgRole.ADMIN,
        )
    )
    return list(admins.all())


async def forget_agent(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """Drop someone's teams, skills and profile in an organization when they leave
    it or stop being staff. Doesn't commit; the membership change does."""
    for model in (TeamMember, AgentSkill, AgentProfile):
        await session.execute(
            delete(model).where(model.organization_id == organization_id, model.user_id == user_id)
        )


def _team_query(organization_id: uuid.UUID) -> Select[tuple[Team]]:
    return (
        select(Team)
        .options(selectinload(Team.members).joinedload(TeamMember.user))
        .where(Team.organization_id == organization_id)
    )


class TeamService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_teams(self, member: OrganizationMember, *, include_inactive: bool) -> list[Team]:
        require_role(member, *STAFF_ROLES)
        query = _team_query(member.organization_id)
        if not include_inactive:
            query = query.where(Team.is_active.is_(True))
        return list((await self.session.scalars(query.order_by(func.lower(Team.name)))).all())

    async def create(self, member: OrganizationMember, data: TeamCreate) -> Team:
        require_role(member, OrgRole.ADMIN)
        team = Team(
            id=uuid.uuid4(),
            organization_id=member.organization_id,
            name=data.name,
            description=data.description,
            is_active=True,
        )
        team.members = []
        self.session.add(team)
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="team",
            entity_id=team.id,
            action="team.created",
            summary=f"Added the team {data.name}",
        )
        await self._commit_or_conflict()
        return team

    async def update(
        self, member: OrganizationMember, team_id: uuid.UUID, data: TeamUpdate
    ) -> Team:
        require_role(member, OrgRole.ADMIN)
        team = await self._get(member, team_id)
        changes = {
            field: {"from": getattr(team, field), "to": value}
            for field, value in data.model_dump(exclude_none=True).items()
            if getattr(team, field) != value
        }
        if not changes:
            return team
        for field, change in changes.items():
            setattr(team, field, change["to"])
        if set(changes) == {"is_active"}:
            verb = "Reactivated" if team.is_active else "Deactivated"
            summary = f"{verb} the team {team.name}"
        else:
            summary = f"Updated the team {team.name}"
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="team",
            entity_id=team.id,
            action="team.updated",
            summary=summary,
            changes=changes,
        )
        await self._commit_or_conflict()
        return team

    async def set_members(
        self, member: OrganizationMember, team_id: uuid.UUID, data: TeamMembersUpdate
    ) -> Team:
        """Replace the team's membership. Everyone must be an admin or agent here."""
        require_role(member, OrgRole.ADMIN)
        team = await self._get(member, team_id, for_update=True)
        wanted = {m.user_id: m.role for m in data.members}
        staff = await self._staff(member.organization_id, wanted)
        if missing := [str(user_id) for user_id in wanted if user_id not in staff]:
            raise AppError(
                "Team members must be admins or agents of this organization",
                code="invalid_team_member",
                details={"user_ids": missing},
            )

        current = {m.user_id: m for m in team.members}
        added, removed, changed = [], [], []
        for user_id, row in current.items():
            if user_id not in wanted:
                team.members.remove(row)
                removed.append(row.user.full_name)
            elif row.role != wanted[user_id]:
                changed.append(f"{row.user.full_name}: {row.role} → {wanted[user_id]}")
                row.role = wanted[user_id]
        for user_id, role in wanted.items():
            if user_id not in current:
                row = TeamMember(organization_id=member.organization_id, user_id=user_id, role=role)
                row.user = staff[user_id]
                team.members.append(row)
                added.append(f"{staff[user_id].full_name} ({role})")
        if not (added or removed or changed):
            return team
        await audit.record(
            self.session,
            organization_id=member.organization_id,
            actor_id=member.user_id,
            entity_type="team",
            entity_id=team.id,
            action="team.members_changed",
            summary=f"Changed the members of the team {team.name}",
            changes={
                key: value
                for key, value in (("added", added), ("removed", removed), ("role", changed))
                if value
            },
        )
        await self.session.commit()
        return team

    # --- Helpers ----------------------------------------------------------------------

    async def _get(
        self, member: OrganizationMember, team_id: uuid.UUID, *, for_update: bool = False
    ) -> Team:
        query = _team_query(member.organization_id).where(Team.id == team_id)
        if for_update:  # serializes concurrent membership edits of one team
            query = query.with_for_update(of=Team)
        team = await self.session.scalar(query)
        if team is None:
            raise NotFoundError("Team not found")
        return team

    async def _staff(
        self, organization_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, User]:
        ids = list(user_ids)
        if not ids:
            return {}
        rows = await self.session.scalars(
            select(User)
            .join(OrganizationMember, OrganizationMember.user_id == User.id)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role.in_(STAFF_ROLES),
                User.id.in_(ids),
            )
        )
        return {user.id: user for user in rows.all()}

    async def _commit_or_conflict(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:  # the case-insensitive unique name index
            await self.session.rollback()
            raise ConflictError(TEAM_EXISTS, code="team_exists") from exc


class AgentService:
    def __init__(self, session: AsyncSession, presence: PresenceTracker) -> None:
        self.session = session
        self.presence = presence

    async def list_agents(
        self, member: OrganizationMember, *, user_ids: Iterable[uuid.UUID] | None = None
    ) -> list[AgentRead]:
        """Every admin and agent of the organization (or just ``user_ids``), by name."""
        require_role(member, *STAFF_ROLES)
        organization_id = member.organization_id
        query = (
            select(OrganizationMember)
            .join(OrganizationMember.user)
            .options(contains_eager(OrganizationMember.user))
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.role.in_(STAFF_ROLES),
            )
            .order_by(func.lower(User.full_name), User.id)
        )
        if user_ids is not None:
            query = query.where(OrganizationMember.user_id.in_(list(user_ids)))
        members = list((await self.session.scalars(query)).all())
        ids = [m.user_id for m in members]
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
        skills: dict[uuid.UUID, list[SkillLevel]] = defaultdict(list)
        for row in (
            await self.session.scalars(
                select(AgentSkill)
                .where(AgentSkill.organization_id == organization_id, AgentSkill.user_id.in_(ids))
                .order_by(AgentSkill.skill)
            )
        ).all():
            skills[row.user_id].append(SkillLevel(skill=row.skill, level=row.level))
        teams: dict[uuid.UUID, list[AgentTeam]] = defaultdict(list)
        for user_id, team_id, name, role in (
            await self.session.execute(
                select(TeamMember.user_id, Team.id, Team.name, TeamMember.role)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    TeamMember.organization_id == organization_id,
                    TeamMember.user_id.in_(ids),
                    Team.is_active.is_(True),
                )
                .order_by(func.lower(Team.name))
            )
        ).all():
            teams[user_id].append(AgentTeam(id=team_id, name=name, role=role))
        workload = await active_ticket_counts(self.session, organization_id, ids)
        online = await self.presence.online(organization_id)

        agents = []
        for m in members:
            profile = profiles.get(m.user_id)
            status = profile.status if profile else AgentStatus.AVAILABLE
            seen = online.get(m.user_id)
            agents.append(
                AgentRead(
                    user_id=m.user_id,
                    full_name=m.user.full_name,
                    email=m.user.email,
                    role=m.role,
                    status=status,
                    status_changed_at=profile.status_changed_at if profile else None,
                    online=seen is not None,
                    last_seen_at=seen,
                    presence=status.value if seen is not None else "offline",
                    max_active_tickets=(
                        profile.max_active_tickets if profile else DEFAULT_MAX_ACTIVE_TICKETS
                    ),
                    active_tickets=workload.get(m.user_id, 0),
                    skills=skills[m.user_id],
                    teams=teams[m.user_id],
                )
            )
        return agents

    async def get_agent(self, member: OrganizationMember, user_id: uuid.UUID) -> AgentRead:
        found = await self.list_agents(member, user_ids=[user_id])
        if not found:
            raise NotFoundError("Agent not found", code="agent_not_found")
        return found[0]

    async def update_profile(
        self, member: OrganizationMember, user_id: uuid.UUID, data: AgentProfileUpdate
    ) -> AgentRead:
        require_role(member, OrgRole.ADMIN)
        before = await self.get_agent(member, user_id)
        profile = await self._profile(member.organization_id, user_id)
        changes: dict[str, dict[str, object]] = {}
        if (
            data.max_active_tickets is not None
            and data.max_active_tickets != profile.max_active_tickets
        ):
            changes["max_active_tickets"] = {
                "from": profile.max_active_tickets,
                "to": data.max_active_tickets,
            }
            profile.max_active_tickets = data.max_active_tickets
        if data.skills is not None:
            old = {s.skill: s.level for s in before.skills}
            new = {s.skill: s.level for s in data.skills}
            if old != new:
                await self._replace_skills(member.organization_id, user_id, old, new)
                changes["skills"] = {"from": _skill_list(old), "to": _skill_list(new)}
        if changes:
            await audit.record(
                self.session,
                organization_id=member.organization_id,
                actor_id=member.user_id,
                entity_type="agent",
                entity_id=user_id,
                action="agent.profile_updated",
                summary=f"Updated {before.full_name}'s capacity and skills",
                changes=changes,
            )
        await self.session.commit()
        return await self.get_agent(member, user_id)

    async def set_presence(self, member: OrganizationMember, status: AgentStatus) -> AgentRead:
        """The caller's own status. Not audited: it changes many times a day."""
        require_role(member, *STAFF_ROLES)
        profile = await self._profile(member.organization_id, member.user_id)
        if profile.status != status:
            profile.status = status
            profile.status_changed_at = utcnow()
        await self.session.commit()
        return await self.get_agent(member, member.user_id)

    # --- Helpers ----------------------------------------------------------------------

    async def _profile(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> AgentProfile:
        """The agent's profile, created with the defaults if missing, locked for update."""
        await self.session.execute(
            insert(AgentProfile)
            .values(id=uuid.uuid4(), organization_id=organization_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["organization_id", "user_id"])
        )
        return (
            await self.session.scalars(
                select(AgentProfile)
                .where(
                    AgentProfile.organization_id == organization_id,
                    AgentProfile.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()

    async def _replace_skills(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        old: dict[str, int],
        new: dict[str, int],
    ) -> None:
        rows = {
            row.skill: row
            for row in (
                await self.session.scalars(
                    select(AgentSkill).where(
                        AgentSkill.organization_id == organization_id,
                        AgentSkill.user_id == user_id,
                    )
                )
            ).all()
        }
        for skill, row in rows.items():
            if skill not in new:
                await self.session.delete(row)
            elif row.level != new[skill]:
                row.level = new[skill]
        for skill, level in new.items():
            if skill not in old:
                self.session.add(
                    AgentSkill(
                        organization_id=organization_id, user_id=user_id, skill=skill, level=level
                    )
                )


def _skill_list(skills: dict[str, int]) -> list[str]:
    return [f"{skill}:{level}" for skill, level in sorted(skills.items())]
