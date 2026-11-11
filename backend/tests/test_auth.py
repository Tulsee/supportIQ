from datetime import UTC, datetime, timedelta

import httpx2
import jwt
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import JWT_ALGORITHM
from app.models import User
from tests.factories import API, DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.anyio

REGISTER = f"{API}/auth/register"
LOGIN = f"{API}/auth/login"
REFRESH = f"{API}/auth/refresh"
LOGOUT = f"{API}/auth/logout"
ME = f"{API}/users/me"
COOKIE = settings.refresh_cookie_name


async def login(
    api: httpx2.AsyncClient, email: str, password: str = DEFAULT_PASSWORD
) -> httpx2.Response:
    return await api.post(LOGIN, json={"email": email, "password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Registration ---------------------------------------------------------------------


async def test_register_creates_user_with_normalized_fields(api: httpx2.AsyncClient) -> None:
    response = await api.post(
        REGISTER,
        json={"email": "Jane.Doe@Example.COM", "password": DEFAULT_PASSWORD, "full_name": " Jane "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "jane.doe@example.com"
    assert body["full_name"] == "Jane"
    assert body["is_active"] is True
    assert "password" not in response.text


async def test_register_rejects_duplicate_email_case_insensitively(
    api: httpx2.AsyncClient,
) -> None:
    await create_user(api, "sam@example.com")

    response = await api.post(
        REGISTER,
        json={"email": "SAM@example.com", "password": DEFAULT_PASSWORD, "full_name": "Sam"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_register_rejects_short_password_without_echoing_it(
    api: httpx2.AsyncClient,
) -> None:
    response = await api.post(
        REGISTER, json={"email": "short@example.com", "password": "s3cr3t", "full_name": "X"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == ["body", "password"]
    assert "s3cr3t" not in response.text


# --- Login and access tokens ------------------------------------------------------------


async def test_login_returns_access_token_and_httponly_refresh_cookie(
    api: httpx2.AsyncClient,
) -> None:
    user = await create_user(api)

    response = await login(api, user.email)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expire_minutes * 60
    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "path=/api/v1/auth" in cookie
    assert "samesite=lax" in cookie
    me = await api.get(ME, headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == user.email


async def test_login_failures_are_indistinguishable(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)

    wrong_password = await login(api, user.email, "wrong-password")
    unknown_email = await login(api, "nobody@example.com")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["message"] == "Invalid email or password"
    assert wrong_password.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic dXNlcjpwYXNz"}],
    ids=["missing", "malformed", "wrong-scheme"],
)
async def test_protected_route_rejects_missing_or_invalid_token(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.get(ME, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("key", "expires_delta"),
    [
        (settings.jwt_secret_key.get_secret_value(), timedelta(minutes=-1)),
        ("some-other-signing-key-of-sufficient-length!", timedelta(minutes=5)),
    ],
    ids=["expired", "forged-signature"],
)
async def test_expired_or_forged_access_token_is_rejected(
    api: httpx2.AsyncClient, key: str, expires_delta: timedelta
) -> None:
    user = await create_user(api)
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": user.id, "type": "access", "iat": now, "exp": now + expires_delta},
        key,
        algorithm=JWT_ALGORITHM,
    )

    response = await api.get(ME, headers=bearer(token))

    assert response.status_code == 401


async def test_update_profile(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)

    response = await api.patch(ME, json={"full_name": "  New Name "}, headers=user.headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"


async def test_deactivated_user_loses_access(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    user = await create_user(api)

    await db_session.execute(update(User).where(User.email == user.email).values(is_active=False))

    assert (await api.get(ME, headers=user.headers)).status_code == 401
    assert (await login(api, user.email)).status_code == 401


# --- Refresh-token rotation ----------------------------------------------------------


async def test_refresh_rotates_the_refresh_token(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)
    await login(api, user.email)
    first_cookie = api.cookies[COOKIE]

    response = await api.post(REFRESH)

    assert response.status_code == 200
    assert api.cookies[COOKIE] != first_cookie
    me = await api.get(ME, headers=bearer(response.json()["access_token"]))
    assert me.status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_the_session(
    api: httpx2.AsyncClient,
) -> None:
    user = await create_user(api)
    await login(api, user.email)
    stolen = api.cookies[COOKIE]
    assert (await api.post(REFRESH)).status_code == 200  # legitimate rotation
    latest = api.cookies[COOKIE]
    api.cookies.clear()

    replay = await api.post(REFRESH, headers={"Cookie": f"{COOKIE}={stolen}"})

    assert replay.status_code == 401
    # The whole login session is revoked, including the legitimate latest token.
    after = await api.post(REFRESH, headers={"Cookie": f"{COOKIE}={latest}"})
    assert after.status_code == 401


async def test_refresh_without_cookie_is_rejected(api: httpx2.AsyncClient) -> None:
    response = await api.post(REFRESH)

    assert response.status_code == 401


async def test_logout_revokes_session_and_clears_cookie(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)
    await login(api, user.email)
    refresh_token = api.cookies[COOKIE]

    response = await api.post(LOGOUT)

    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    api.cookies.clear()
    reuse = await api.post(REFRESH, headers={"Cookie": f"{COOKIE}={refresh_token}"})
    assert reuse.status_code == 401
