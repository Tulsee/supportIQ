"""Helpers that create test data through the public API (black-box)."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

from ai_engine.chunking.chunker import ChunkingConfig
from ai_engine.generation.llm import Completion, LLMError, ModelState, ModelStatus, Prompt
from app.core.email import EmailMessage
from app.services.ingestion import IngestionLimits

API = "/api/v1"
# Small chunks, so short test documents still produce several of them.
TEST_INGESTION_LIMITS = IngestionLimits(
    max_pages=50, max_characters=200_000, chunking=ChunkingConfig(size=40, overlap=8)
)
DEFAULT_PASSWORD = "correct-horse-battery"
_TOKEN_IN_LINK = re.compile(r"#token=([A-Za-z0-9_\-]+)")


@dataclass
class Outbox:
    """Test email sender: records messages instead of delivering them."""

    messages: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    def last_to(self, email: str) -> EmailMessage:
        return next(m for m in reversed(self.messages) if m.to == email)

    def invitation_token(self, email: str) -> str:
        match = _TOKEN_IN_LINK.search(self.last_to(email).text)
        assert match, "no invitation link in email"
        return match.group(1)


@dataclass
class RecordingQueue:
    """Test ingestion queue: records jobs instead of sending them to a worker.
    Set ``unavailable`` to simulate a broker outage."""

    jobs: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    unavailable: bool = False

    async def enqueue(self, document_id: uuid.UUID, ingestion_id: uuid.UUID) -> None:
        if self.unavailable:
            from app.workers.queue import QueueUnavailableError

            raise QueueUnavailableError("broker down")
        self.jobs.append((document_id, ingestion_id))


@dataclass
class StaticWorkerRegistry:
    """Test stand-in: reports a fixed number of live ingestion workers
    (None: couldn't be determined)."""

    count: int | None = 1

    async def live_workers(self) -> int | None:
        return self.count


@dataclass
class ScriptedLLM:
    """Stand-in language model: replies with ``reply`` (text, or a function of the
    prompt) or raises ``error``, and keeps every prompt it was given."""

    reply: str | Callable[[Prompt], str] = "Replace the fan module [1]."
    error: LLMError | None = None
    state: ModelState = "ready"
    prompt_token_budget: int | None = None
    prompts: list[Prompt] = field(default_factory=list)

    provider = "test"
    model = "test/scripted"

    @property
    def config(self) -> dict[str, Any]:
        return {"temperature": 0.0}

    async def complete(self, prompt: Prompt) -> Completion:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        text = self.reply(prompt) if callable(self.reply) else self.reply
        return Completion(
            text=text,
            model="test/scripted@v1",
            stop_reason="stop",
            input_tokens=321,
            output_tokens=12,
            model_version="sha256:test",
            timings_ms={"prompt": 3.0, "output": 2.0, "total": 5.0},
        )

    async def status(self) -> ModelStatus:
        return ModelStatus(self.state, None if self.state == "ready" else "not running")


@dataclass(frozen=True)
class AuthedUser:
    id: str
    email: str
    headers: dict[str, str]


async def create_user(
    api: httpx2.AsyncClient,
    email: str | None = None,
    *,
    password: str = DEFAULT_PASSWORD,
    full_name: str = "Test User",
) -> AuthedUser:
    """Register and log in a user. Clears the client's cookie jar afterwards so
    refresh cookies never leak between users in one test."""
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    registered = await api.post(
        f"{API}/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert registered.status_code == 201, registered.text
    logged_in = await api.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200, logged_in.text
    api.cookies.clear()
    token = logged_in.json()["access_token"]
    return AuthedUser(
        id=registered.json()["id"],
        email=registered.json()["email"],
        headers={"Authorization": f"Bearer {token}"},
    )


async def create_org(
    api: httpx2.AsyncClient, owner: AuthedUser, name: str = "Acme Support", **extra: Any
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/organizations", json={"name": name, **extra}, headers=owner.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def invite(
    api: httpx2.AsyncClient, admin: AuthedUser, org_id: str, email: str, role: str
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/organizations/{org_id}/invitations",
        json={"email": email, "role": role},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@dataclass(frozen=True)
class Workspace:
    """An organization with one member of each role."""

    org_id: str
    admin: AuthedUser
    agent: AuthedUser
    customer: AuthedUser  # a member with the customer role


def in_org(user: AuthedUser, org_id: str) -> dict[str, str]:
    """Headers for tenant-scoped endpoints: the user's token plus the organization."""
    return {**user.headers, "X-Organization-Id": org_id}


async def create_customer(
    api: httpx2.AsyncClient,
    staff: AuthedUser,
    org_id: str,
    name: str = "Casey Customer",
    email: str | None = None,
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/customers", json={"name": name, "email": email}, headers=in_org(staff, org_id)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_ticket(
    api: httpx2.AsyncClient,
    actor: AuthedUser,
    org_id: str,
    *,
    subject: str = "Printer is on fire",
    message: str = "It started smoking after the update.",
    **extra: Any,
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/tickets",
        json={"subject": subject, "message": message, **extra},
        headers=in_org(actor, org_id),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def add_member(
    api: httpx2.AsyncClient,
    outbox: Outbox,
    admin: AuthedUser,
    org_id: str,
    user: AuthedUser,
    role: str,
) -> dict[str, Any]:
    """Invite ``user`` and accept as them (the only way to join an organization).
    Returns their membership as listed by the admin."""
    await invite(api, admin, org_id, user.email, role)
    accepted = await api.post(
        f"{API}/invitations/accept",
        json={"token": outbox.invitation_token(user.email)},
        headers=user.headers,
    )
    assert accepted.status_code == 200, accepted.text
    members = await api.get(
        f"{API}/organizations/{org_id}/members", params={"limit": 100}, headers=admin.headers
    )
    return next(m for m in members.json()["items"] if m["user_id"] == user.id)
