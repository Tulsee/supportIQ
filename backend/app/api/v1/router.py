"""Aggregates all v1 endpoint routers. Mounted under ``settings.api_v1_prefix``."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    auth,
    customers,
    health,
    invitations,
    organizations,
    tickets,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(invitations.org_router)
api_router.include_router(invitations.public_router)
api_router.include_router(customers.router)
api_router.include_router(tickets.router)
api_router.include_router(admin.router)
