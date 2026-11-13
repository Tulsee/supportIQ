"""Analytics for the organization in ``X-Organization-Id`` (staff, Module 7)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, TenantMembership
from app.schemas.analytics import (
    AIPerformance,
    Overview,
    RoutingAnalytics,
    SelfServiceAnalytics,
    SlaDetail,
    TriageAnalytics,
)
from app.schemas.common import error_responses
from app.services.analytics import AnalyticsService

router = APIRouter(
    prefix="/analytics", tags=["analytics"], responses=error_responses(401, 403, 404, 422)
)

Days = Annotated[int, Query(ge=1, le=365, description="Trailing window, in days.")]


@router.get("/overview", summary="Support operations")
async def overview(member: TenantMembership, session: DbSession, days: Days = 30) -> Overview:
    """Open tickets, volume per day, median first-response and resolution times."""
    return Overview(**await AnalyticsService(session).overview(member, days))


@router.get("/sla", summary="Service levels in depth")
async def sla_detail(member: TenantMembership, session: DbSession, days: Days = 30) -> SlaDetail:
    """Compliance by priority, team and agent; median business time to meet each
    target; due-soon and breached transitions per day; pause reasons."""
    return SlaDetail(**await AnalyticsService(session).sla_detail(member, days))


@router.get("/triage", summary="AI ticket triage")
async def triage(member: TenantMembership, session: DbSession, days: Days = 30) -> TriageAnalytics:
    """Coverage (how often triage was confident), accuracy of confident predictions and
    of the top guess when unsure (from people's reviews), per category, and latency."""
    return TriageAnalytics(**await AnalyticsService(session).triage(member, days))


@router.get("/self-service", summary="The customer assistant")
async def self_service(
    member: TenantMembership, session: DbSession, days: Days = 30
) -> SelfServiceAnalytics:
    """Conversations customers started in the period and how they ended (resolved only
    when the customer confirmed an answer helped; handed to a person), the assistant's
    replies and why it couldn't answer, and the latest questions it missed."""
    return SelfServiceAnalytics(**await AnalyticsService(session).self_service(member, days))


@router.get("/routing", summary="Automatic routing")
async def routing(
    member: TenantMembership, session: DbSession, days: Days = 30
) -> RoutingAnalytics:
    """Routing decisions by outcome (and in shadow mode), the share of tickets assigned
    automatically and the median time to a first assignment, automatic assignments a
    person changed within an hour, how often people agreed with shadow decisions, and
    each agent's workload."""
    return RoutingAnalytics(**await AnalyticsService(session).routing(member, days))


@router.get("/ai-performance", summary="AI performance")
async def ai_performance(
    member: TenantMembership, session: DbSession, days: Days = 30
) -> AIPerformance:
    """AI runs per day, answer outcomes and policy decisions, latency, staff feedback,
    model usage, and knowledge gaps (questions not answered confidently)."""
    return AIPerformance(**await AnalyticsService(session).ai_performance(member, days))
