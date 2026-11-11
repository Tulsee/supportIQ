"""Liveness and readiness probes (used by Docker/orchestrator health checks)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.config import settings
from app.core.database import ping_database
from app.core.redis import ping_redis
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness probe")
async def liveness() -> HealthResponse:
    """Returns 200 while the process is serving requests. Does not touch dependencies."""
    return HealthResponse(
        status="ok",
        service=settings.project_name,
        version=settings.version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "A required dependency is unavailable.",
        }
    },
)
async def readiness(
    response: Response,
    database_ok: Annotated[bool, Depends(ping_database)],
    redis_ok: Annotated[bool, Depends(ping_redis)],
) -> ReadinessResponse:
    """Returns 200 when every required dependency is reachable, otherwise 503."""
    checks = {
        "database": "ok" if database_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
    if not (database_ok and redis_ok):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready", checks=checks)
    return ReadinessResponse(status="ready", checks=checks)
