"""Registration, login, token refresh, password change, and logout.

The access token is returned in the response body (send it as
``Authorization: Bearer <token>``). The refresh token is only ever set as an
httpOnly cookie scoped to the auth endpoints, so page scripts cannot read it.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import ClientIp, CurrentUser, DbSession, RateLimiterDep
from app.core.config import settings
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest, TokenResponse
from app.schemas.common import error_responses
from app.schemas.users import UserRead
from app.services.auth import AuthService, IssuedTokens

router = APIRouter(
    prefix="/auth", tags=["auth"], responses=error_responses(400, 401, 409, 422, 429)
)

RefreshCookie = Annotated[
    str | None,
    Cookie(alias=settings.refresh_cookie_name, description="Set by login/refresh."),
]


def _respond_with_tokens(response: Response, tokens: IssuedTokens) -> TokenResponse:
    response.set_cookie(
        settings.refresh_cookie_name,
        tokens.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )
    return TokenResponse(access_token=tokens.access_token, expires_in=tokens.access_expires_in)


@router.post("/register", status_code=status.HTTP_201_CREATED, summary="Create an account")
async def register(
    body: RegisterRequest, session: DbSession, limiter: RateLimiterDep, client_ip: ClientIp
) -> UserRead:
    """Rate limited per client IP."""
    user = await AuthService(session, limiter).register(body, client_ip=client_ip)
    return UserRead.model_validate(user)


@router.post("/login", summary="Sign in")
async def login(
    body: LoginRequest,
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    client_ip: ClientIp,
) -> TokenResponse:
    """Returns an access token and sets the refresh-token cookie.

    Rate limited per client IP, and per account after repeated failures (429 with
    a ``Retry-After`` header).
    """
    tokens = await AuthService(session, limiter).login(
        body.email, body.password, client_ip=client_ip
    )
    return _respond_with_tokens(response, tokens)


@router.post("/refresh", summary="Get a new access token")
async def refresh(
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    refresh_token: RefreshCookie = None,
) -> TokenResponse:
    """Rotates the refresh-token cookie. Reusing an old refresh token ends the session."""
    tokens = await AuthService(session, limiter).refresh(refresh_token)
    return _respond_with_tokens(response, tokens)


@router.post("/change-password", summary="Change your password")
async def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser,
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
) -> TokenResponse:
    """Requires the current password (400 `invalid_current_password` if wrong; repeated
    failures are rate limited). Signs out every other session and returns a fresh
    access token and refresh-token cookie for this one."""
    tokens = await AuthService(session, limiter).change_password(
        user, body.current_password, body.new_password
    )
    return _respond_with_tokens(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    response: Response,
    session: DbSession,
    limiter: RateLimiterDep,
    refresh_token: RefreshCookie = None,
) -> None:
    """Revokes the login session and clears the refresh-token cookie."""
    await AuthService(session, limiter).logout(refresh_token)
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )
