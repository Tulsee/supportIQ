"""Aggregates all v1 endpoint routers. Mounted under ``settings.api_v1_prefix``."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    ai,
    analytics,
    auth,
    categories,
    customers,
    documents,
    health,
    invitations,
    notifications,
    organizations,
    realtime,
    ticket_ai,
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
api_router.include_router(categories.router)
api_router.include_router(ticket_ai.router)
api_router.include_router(documents.router)
api_router.include_router(ai.router)
api_router.include_router(analytics.router)
api_router.include_router(notifications.router)
api_router.include_router(notifications.audit_router)
api_router.include_router(realtime.router)
api_router.include_router(admin.router)
