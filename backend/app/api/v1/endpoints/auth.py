"""Registration, login, token refresh, and logout.

The access token is returned in the response body (send it as
``Authorization: Bearer <token>``). The refresh token is only ever set as an
httpOnly cookie scoped to the auth endpoints, so page scripts cannot read it.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status

from app.api.deps import DbSession
from app.core.config import settings
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.common import error_responses
from app.schemas.users import UserRead
from app.services.auth import AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"], responses=error_responses(401, 409, 422))

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
async def register(body: RegisterRequest, session: DbSession) -> UserRead:
    user = await AuthService(session).register(body)
    return UserRead.model_validate(user)


@router.post("/login", summary="Sign in")
async def login(body: LoginRequest, response: Response, session: DbSession) -> TokenResponse:
    """Returns an access token and sets the refresh-token cookie."""
    tokens = await AuthService(session).login(body.email, body.password)
    return _respond_with_tokens(response, tokens)


@router.post("/refresh", summary="Get a new access token")
async def refresh(
    response: Response, session: DbSession, refresh_token: RefreshCookie = None
) -> TokenResponse:
    """Rotates the refresh-token cookie. Reusing an old refresh token ends the session."""
    tokens = await AuthService(session).refresh(refresh_token)
    return _respond_with_tokens(response, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(
    response: Response, session: DbSession, refresh_token: RefreshCookie = None
) -> None:
    """Revokes the login session and clears the refresh-token cookie."""
    await AuthService(session).logout(refresh_token)
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )
