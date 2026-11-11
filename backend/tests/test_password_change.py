import httpx2
import pytest

from app.core.config import settings
from tests.factories import API, DEFAULT_PASSWORD, create_user

pytestmark = pytest.mark.anyio

CHANGE = f"{API}/auth/change-password"
LOGIN = f"{API}/auth/login"
REFRESH = f"{API}/auth/refresh"
ME = f"{API}/users/me"
COOKIE = settings.refresh_cookie_name
NEW_PASSWORD = "a-brand-new-passphrase"


async def change(
    api: httpx2.AsyncClient, headers: dict[str, str], current: str, new: str = NEW_PASSWORD
) -> httpx2.Response:
    return await api.post(
        CHANGE, json={"current_password": current, "new_password": new}, headers=headers
    )


async def test_change_password_swaps_credentials_and_issues_a_new_session(
    api: httpx2.AsyncClient,
) -> None:
    user = await create_user(api)

    response = await change(api, user.headers, DEFAULT_PASSWORD)

    assert response.status_code == 200
    assert "path=/api/v1/auth" in response.headers["set-cookie"].lower()
    me = await api.get(ME, headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert me.status_code == 200
    assert (await api.post(REFRESH)).status_code == 200  # the new cookie works
    api.cookies.clear()
    old = await api.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    new = await api.post(LOGIN, json={"email": user.email, "password": NEW_PASSWORD})
    assert old.status_code == 401
    assert new.status_code == 200


async def test_change_password_signs_out_other_sessions(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)
    await api.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    other_device = api.cookies[COOKIE]
    api.cookies.clear()

    assert (await change(api, user.headers, DEFAULT_PASSWORD)).status_code == 200

    api.cookies.clear()
    renew = await api.post(REFRESH, headers={"Cookie": f"{COOKIE}={other_device}"})
    assert renew.status_code == 401


async def test_wrong_current_password_is_400_not_401(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)

    response = await change(api, user.headers, "not-my-password")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_current_password"
    # Still signed in, and the password is unchanged.
    assert (await api.get(ME, headers=user.headers)).status_code == 200
    login = await api.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})
    assert login.status_code == 200


async def test_repeated_wrong_current_passwords_are_rate_limited(
    api: httpx2.AsyncClient,
) -> None:
    user = await create_user(api)
    for _ in range(settings.login_account_failure_limit):
        assert (await change(api, user.headers, "guess")).status_code == 400

    # Locked even with the right password, until the window passes.
    response = await change(api, user.headers, DEFAULT_PASSWORD)

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


async def test_new_password_must_differ_and_follow_the_policy(api: httpx2.AsyncClient) -> None:
    user = await create_user(api)

    same = await change(api, user.headers, DEFAULT_PASSWORD, DEFAULT_PASSWORD)
    short = await change(api, user.headers, DEFAULT_PASSWORD, "Xq7!zp")

    assert same.status_code == 400
    assert same.json()["error"]["code"] == "password_unchanged"
    assert short.status_code == 422
    assert "Xq7!zp" not in short.text  # never echoed back


async def test_change_password_requires_authentication(api: httpx2.AsyncClient) -> None:
    response = await change(api, {}, DEFAULT_PASSWORD)

    assert response.status_code == 401
