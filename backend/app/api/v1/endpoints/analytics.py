"""Analytics for the organization in ``X-Organization-Id`` (staff, Module 7)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, TenantMembership
from app.schemas.analytics import AIPerformance, Overview, RoutingAnalytics
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
