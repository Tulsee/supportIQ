"""Aggregates all v1 endpoint routers. Mounted under ``settings.api_v1_prefix``."""

from fastapi import APIRouter

from app.api.v1.endpoints import health

api_router = APIRouter()
api_router.include_router(health.router)
