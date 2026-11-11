"""Helpers that create test data through the public API (black-box)."""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx2

API = "/api/v1"
DEFAULT_PASSWORD = "correct-horse-battery"


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


async def add_member(
    api: httpx2.AsyncClient, admin: AuthedUser, org_id: str, user: AuthedUser, role: str
) -> dict[str, Any]:
    response = await api.post(
        f"{API}/organizations/{org_id}/members",
        json={"email": user.email, "role": role},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
