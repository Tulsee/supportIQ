"""Routing rules, the simulator, and routing on tickets (Module 10, phase 6), in the
organization in ``X-Organization-Id``. Staff read rules and decisions and can reroute
an unassigned ticket; admins manage rules and run the simulator."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    EventBrokerDep,
    NotificationsDep,
    PresenceDep,
    TenantMembership,
)
from app.schemas.common import error_responses
from app.schemas.routing import (
    DecisionRead,
    RuleCreate,
    RuleOrder,
    RuleRead,
    RuleUpdate,
    SimulationRead,
    SimulationRequest,
)
from app.services.routing import RoutingService, RuleService

router = APIRouter(
    prefix="/routing", tags=["routing"], responses=error_responses(401, 403, 404, 409, 422)
)
ticket_router = APIRouter(
    prefix="/tickets", tags=["routing"], responses=error_responses(401, 403, 404, 409, 422)
)


@router.get("/rules", summary="List routing rules in the order they're tried")
async def list_rules(member: TenantMembership, session: DbSession) -> list[RuleRead]:
    return [RuleRead.build(r) for r in await RuleService(session).list_rules(member)]


@router.post("/rules", status_code=status.HTTP_201_CREATED, summary="Add a routing rule (admin)")
async def create_rule(body: RuleCreate, member: TenantMembership, session: DbSession) -> RuleRead:
    """Added last. 400 `routing_rule_invalid` for an inactive or unknown team or
    unknown category keys; 409 `routing_rule_exists` if the name is taken."""
    return RuleRead.build(await RuleService(session).create(member, body))


@router.put("/rules/order", summary="Set the order rules are tried in (admin)")
async def reorder_rules(
    body: RuleOrder, member: TenantMembership, session: DbSession
) -> list[RuleRead]:
    """`rule_ids` must list every rule of the organization exactly once."""
    return [RuleRead.build(r) for r in await RuleService(session).reorder(member, body)]


@router.patch("/rules/{rule_id}", summary="Change or turn off a routing rule (admin)")
async def update_rule(
    rule_id: uuid.UUID, body: RuleUpdate, member: TenantMembership, session: DbSession
) -> RuleRead:
    return RuleRead.build(await RuleService(session).update(member, rule_id, body))


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a routing rule (admin)",
)
async def delete_rule(rule_id: uuid.UUID, member: TenantMembership, session: DbSession) -> None:
    """Decisions it made keep its name and version."""
    await RuleService(session).delete(member, rule_id)


@router.post("/simulate", summary="What routing would do now, without doing it (admin)")
async def simulate(
    body: SimulationRequest, member: TenantMembership, session: DbSession, presence: PresenceDep
) -> SimulationRead:
    """For an existing ticket (`ticket_id`) or a hypothetical one. Uses the active rules
    and agents' current status, presence and workload. Writes nothing."""
    return await RoutingService(session, presence).simulate(member, body)


@ticket_router.get("/{ticket_id}/routing", summary="The ticket's routing decisions (staff)")
async def ticket_routing(
    ticket_id: uuid.UUID, member: TenantMembership, session: DbSession, presence: PresenceDep
) -> list[DecisionRead]:
    """Newest first, each with the rules tried, the candidates and an explanation."""
    decisions = await RoutingService(session, presence).decisions(member, ticket_id)
    return [DecisionRead.build(d, actor) for d, actor in decisions]


@ticket_router.post("/{ticket_id}/reroute", summary="Run routing on the ticket now (staff)")
async def reroute_ticket(
    ticket_id: uuid.UUID,
    member: TenantMembership,
    user: CurrentUser,
    session: DbSession,
    presence: PresenceDep,
    events: EventBrokerDep,
    notifications: NotificationsDep,
) -> DecisionRead:
    """Applies the rules now, whatever the organization's routing mode. 409
    `not_routable` if the ticket is assigned (unassign it first), resolved or closed."""
    service = RoutingService(session, presence, events, notifications)
    decision = await service.reroute(member, user, ticket_id)
    return DecisionRead.build(decision, user)
