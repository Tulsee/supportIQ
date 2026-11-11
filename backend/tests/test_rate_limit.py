import uuid

import httpx2
import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.core.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from tests.factories import API, DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.anyio

LOGIN = f"{API}/auth/login"
REGISTER = f"{API}/auth/register"


async def login(api: httpx2.AsyncClient, email: str, password: str) -> httpx2.Response:
    return await api.post(LOGIN, json={"email": email, "password": password})


# --- Login: per-account lockout ------------------------------------------------------


async def test_account_is_locked_after_repeated_failures(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)
    limit = settings.login_account_failure_limit

    failures = [await login(api, user.email, "wrong-password") for _ in range(limit)]
    locked = await login(api, user.email, DEFAULT_PASSWORD)  # even the right password

    assert {r.status_code for r in failures} == {401}
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"
    assert 0 < int(locked.headers["retry-after"]) <= settings.login_account_window_seconds
    other_user = await create_user(api)  # other accounts are unaffected
    assert (await login(api, other_user.email, DEFAULT_PASSWORD)).status_code == 200


async def test_unknown_emails_lock_exactly_like_real_ones(api: httpx2.AsyncClient) -> None:
    limit = settings.login_account_failure_limit

    for _ in range(limit):
        assert (await login(api, "ghost@example.com", "guess")).status_code == 401
    response = await login(api, "ghost@example.com", "guess")

    assert response.status_code == 429  # same as a real account: no enumeration


async def test_successful_login_resets_the_failure_count(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)
    almost = settings.login_account_failure_limit - 1

    for _ in range(almost):
        await login(api, user.email, "wrong-password")
    assert (await login(api, user.email, DEFAULT_PASSWORD)).status_code == 200
    for _ in range(almost):
        await login(api, user.email, "wrong-password")

    assert (await login(api, user.email, DEFAULT_PASSWORD)).status_code == 200


# --- Per-IP limits --------------------------------------------------------------------


async def test_login_is_limited_per_ip(
    api: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_ip_limit", 3)

    responses = [await login(api, f"user{i}@example.com", "guess") for i in range(4)]

    assert [r.status_code for r in responses] == [401, 401, 401, 429]


async def test_registration_is_limited_per_ip(
    api: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "register_ip_limit", 2)

    statuses = [
        (
            await api.post(
                REGISTER,
                json={
                    "email": f"new{i}@example.com",
                    "password": DEFAULT_PASSWORD,
                    "full_name": "New",
                },
            )
        ).status_code
        for i in range(3)
    ]

    assert statuses == [201, 201, 429]


# --- Limiter implementations -----------------------------------------------------------


async def test_in_memory_limiter_window_resets() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])

    assert (await limiter.hit("k", limit=2, window_seconds=60)).allowed
    assert (await limiter.hit("k", limit=2, window_seconds=60)).allowed
    blocked = await limiter.hit("k", limit=2, window_seconds=60)
    assert not blocked.allowed
    assert blocked.retry_after == 60

    now[0] += 60
    assert (await limiter.hit("k", limit=2, window_seconds=60)).allowed


async def test_redis_limiter_counts_checks_and_resets() -> None:
    client = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
    limiter = RedisRateLimiter(client, prefix=f"test:{uuid.uuid4().hex}:")
    try:
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed
        await limiter.hit("k", limit=2, window_seconds=60)
        second = await limiter.hit("k", limit=2, window_seconds=60)
        assert second.allowed
        third = await limiter.hit("k", limit=2, window_seconds=60)
        assert not third.allowed
        assert 0 < third.retry_after <= 60
        assert not (await limiter.check("k", limit=2, window_seconds=60)).allowed

        await limiter.reset("k")
        assert (await limiter.check("k", limit=2, window_seconds=60)).allowed
    finally:
        await limiter.reset("k")
        await client.aclose()


async def test_redis_limiter_fails_open_when_redis_is_down() -> None:
    client = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.5)
    limiter = RedisRateLimiter(client)
    try:
        result = await limiter.hit("k", limit=0, window_seconds=60)
    finally:
        await client.aclose()

    assert result.allowed
