"""Shared FastAPI dependencies, exposed as ``Annotated`` aliases for endpoint signatures.

Request context is resolved in layers:

1. ``CurrentUser``: who is calling (validated access token, active user).
2. Which organization they act in, which also proves membership:
   - ``OrgMembership``: from the ``{org_id}`` path parameter, for
     ``/organizations/{org_id}/...`` routes, where the organization *is* the resource.
   - ``TenantMembership``: from the ``X-Organization-Id`` header, for tenant-scoped
     resources (customers, tickets, documents, ...).
3. What they may do: decided by services via ``require_role(membership, ...)``.

Steps 1 and 2 also set the database row-level-security context (current user,
current organization), so Postgres enforces the same tenant boundary.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ai_engine.embeddings.embedder import Embedder
from ai_engine.evidence.nli import NliModel
from ai_engine.generation.llm import LLMClient
from ai_engine.reranking.cross_encoder import Reranker
from ai_engine.retrieval.vector_store import QdrantVectorStore
from app.core.database import get_db_session, set_rls_context
from app.core.email import EmailSender, get_email_sender
from app.core.exceptions import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.knowledge import (
    get_embedder,
    get_nli_model,
    get_qdrant_client,
    get_reranker,
    vector_store_for,
)
from app.core.llm import get_llm_client
from app.core.rate_limit import RateLimiter, RedisRateLimiter
from app.core.redis import get_redis
from app.core.security import InvalidTokenError, decode_access_token
from app.core.storage import ObjectStorage, get_object_storage
from app.models import OrganizationMember, User
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository
from app.services.notifications import NotificationService
from app.websocket.broker import EventBroker, RedisEventBroker
from app.websocket.presence import PresenceTracker, RedisPresence
from app.workers.heartbeat import RedisWorkerRegistry, WorkerRegistry
from app.workers.queue import (
    CeleryIngestionQueue,
    CeleryNotificationMailer,
    CeleryRoutingQueue,
    CeleryTriageQueue,
    IngestionQueue,
    NotificationMailer,
    RoutingQueue,
    TriageQueue,
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter(get_redis())


def get_client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers and
    # --forwarded-allow-ips so request.client reflects the real client address.
    return request.client.host if request.client else "unknown"


def get_event_broker() -> EventBroker:
    return RedisEventBroker(get_redis())


def get_presence() -> PresenceTracker:
    return RedisPresence(get_redis())


def get_ingestion_queue() -> IngestionQueue:
    return CeleryIngestionQueue()


def get_routing_queue() -> RoutingQueue:
    return CeleryRoutingQueue()


def get_triage_queue() -> TriageQueue:
    return CeleryTriageQueue()


def get_notification_mailer() -> NotificationMailer:
    return CeleryNotificationMailer()


def get_worker_registry() -> WorkerRegistry:
    return RedisWorkerRegistry(get_redis())


def get_embedder_dep() -> Embedder:
    return get_embedder()


def get_reranker_dep() -> Reranker:
    return get_reranker()


def get_llm_client_dep() -> LLMClient:
    return get_llm_client()


def get_nli_model_dep() -> NliModel | None:
    return get_nli_model()


def get_vector_store(embedder: Annotated[Embedder, Depends(get_embedder_dep)]) -> QdrantVectorStore:
    return vector_store_for(get_qdrant_client(), embedder)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
EventBrokerDep = Annotated[EventBroker, Depends(get_event_broker)]
PresenceDep = Annotated[PresenceTracker, Depends(get_presence)]
StorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]
IngestionQueueDep = Annotated[IngestionQueue, Depends(get_ingestion_queue)]
NotificationMailerDep = Annotated[NotificationMailer, Depends(get_notification_mailer)]
RoutingQueueDep = Annotated[RoutingQueue, Depends(get_routing_queue)]
TriageQueueDep = Annotated[TriageQueue, Depends(get_triage_queue)]
WorkerRegistryDep = Annotated[WorkerRegistry, Depends(get_worker_registry)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder_dep)]
RerankerDep = Annotated[Reranker, Depends(get_reranker_dep)]
VectorStoreDep = Annotated[QdrantVectorStore, Depends(get_vector_store)]
LLMClientDep = Annotated[LLMClient, Depends(get_llm_client_dep)]
NliModelDep = Annotated[NliModel | None, Depends(get_nli_model_dep)]
EmailSenderDep = Annotated[EmailSender, Depends(get_email_sender)]
ClientIp = Annotated[str, Depends(get_client_ip)]

_bearer_scheme = HTTPBearer(
    auto_error=False, description="Access token from `POST /api/v1/auth/login`."
)


async def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        raise AuthenticationError("Invalid or expired access token") from None
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired access token")
    await set_rls_context(session, user_id=user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_superadmin(user: CurrentUser) -> User:
    if not user.is_superadmin:
        raise PermissionDeniedError("Platform administrator access required")
    return user


SuperAdmin = Annotated[User, Depends(require_superadmin)]


async def _membership_or_404(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> OrganizationMember:
    membership = await OrganizationRepository(session).get_membership(organization_id, user.id)
    if membership is None:
        # 404 rather than 403: don't reveal that an inaccessible organization exists.
        raise NotFoundError("Organization not found")
    await set_rls_context(session, organization_id=organization_id)
    return membership


async def get_org_membership(
    org_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> OrganizationMember:
    return await _membership_or_404(session, user, org_id)


async def get_tenant_membership(
    user: CurrentUser,
    session: DbSession,
    organization_id: Annotated[
        uuid.UUID,
        Header(alias="X-Organization-Id", description="The organization to act in."),
    ],
) -> OrganizationMember:
    return await _membership_or_404(session, user, organization_id)


OrgMembership = Annotated[OrganizationMember, Depends(get_org_membership)]
TenantMembership = Annotated[OrganizationMember, Depends(get_tenant_membership)]


@dataclass(frozen=True)
class PageParams:
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum items to return.")] = 20
    offset: Annotated[int, Query(ge=0, description="Number of items to skip.")] = 0


Pagination = Annotated[PageParams, Depends()]


def get_notification_service(
    session: DbSession, events: EventBrokerDep, mailer: NotificationMailerDep
) -> NotificationService:
    """Writes notifications with a change; pings and emails after the commit."""
    return NotificationService(session, events=events, mailer=mailer)


NotificationsDep = Annotated[NotificationService, Depends(get_notification_service)]
