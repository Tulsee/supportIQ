"""Helpers that create test data through the public API (black-box)."""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx2

from app.core.email import EmailMessage

API = "/api/v1"
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
