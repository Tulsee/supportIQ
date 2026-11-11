"""Registration, login (with rate limiting), refresh-token rotation, password change."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from anyio import to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, AuthenticationError, ConflictError, TooManyRequestsError
from app.core.rate_limit import RateLimiter
from app.core.security import (
    create_access_token,
    generate_token,
    hash_password,
    hash_token,
    utcnow,
    verify_password,
)
from app.models import RefreshToken, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.auth import RegisterRequest

INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_TAKEN = "An account with this email already exists"
WRONG_CURRENT_PASSWORD = "Your current password is incorrect"
PASSWORD_UNCHANGED = "Choose a password that differs from your current one"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


def _account_key(email: str) -> str:
    # Hash the email so the limiter's store never holds raw addresses.
    return "login:account:" + hashlib.sha256(email.encode()).hexdigest()[:32]


class AuthService:
    def __init__(self, session: AsyncSession, rate_limiter: RateLimiter) -> None:
        self.session = session
        self.rate_limiter = rate_limiter
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register(self, data: RegisterRequest, *, client_ip: str) -> User:
        await self._throttle(
            f"register:ip:{client_ip}",
            limit=settings.register_ip_limit,
            window_seconds=settings.register_ip_window_seconds,
            message="Too many sign-up attempts from your network. Try again later.",
        )
        if await self.users.get_by_email(data.email) is not None:
            raise ConflictError(EMAIL_TAKEN)
        # argon2 is deliberately CPU-heavy; keep it off the event loop.
        password_hash = await to_thread.run_sync(hash_password, data.password)
        user = User(email=data.email, password_hash=password_hash, full_name=data.full_name)
        self.users.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:  # concurrent registration with the same email
            await self.session.rollback()
            raise ConflictError(EMAIL_TAKEN) from exc
        return user

    async def login(self, email: str, password: str, *, client_ip: str) -> IssuedTokens:
        """Two limits: attempts per IP (slows broad credential stuffing) and failed
        attempts per account (slows guessing one account from many IPs). Unknown
        emails are counted exactly like real ones, so lockouts reveal nothing."""
        await self._throttle(
            f"login:ip:{client_ip}",
            limit=settings.login_ip_limit,
            window_seconds=settings.login_ip_window_seconds,
            message="Too many sign-in attempts from your network. Try again later.",
        )
        account_key = _account_key(email)
        account_limit = {
            "limit": settings.login_account_failure_limit,
            "window_seconds": settings.login_account_window_seconds,
        }
        lockout = await self.rate_limiter.check(account_key, **account_limit)
        if not lockout.allowed:
            raise TooManyRequestsError(
                "Too many failed sign-in attempts for this account. Try again later.",
                retry_after=lockout.retry_after,
            )

        user = await self.users.get_by_email(email)
        password_ok = await to_thread.run_sync(
            verify_password, password, user.password_hash if user else None
        )
        # One message for every failure, so responses don't reveal which emails exist.
        if user is None or not password_ok or not user.is_active:
            await self.rate_limiter.hit(account_key, **account_limit)
            raise AuthenticationError(INVALID_CREDENTIALS)

        await self.rate_limiter.reset(account_key)
        tokens = self._issue_tokens(user.id, family_id=uuid.uuid4())
        await self.session.commit()
        return tokens

    async def refresh(self, raw_token: str | None) -> IssuedTokens:
        """Exchange a refresh token for a new access token and a new refresh token."""
        if not raw_token:
            raise AuthenticationError("Missing refresh token")
        now = utcnow()
        record = await self.refresh_tokens.get_by_hash(hash_token(raw_token), for_update=True)
        if record is None:
            raise AuthenticationError("Invalid refresh token")
        if record.revoked_at is not None:
            # An already-rotated token was presented again: assume it was stolen and
            # end the whole login session for both the attacker and the user.
            await self.refresh_tokens.revoke_family(record.family_id, now)
            await self.session.commit()
            raise AuthenticationError("Refresh token was already used; please sign in again")
        if record.expires_at <= now:
            raise AuthenticationError("Refresh token expired; please sign in again")
        user = await self.users.get(record.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid refresh token")

        record.revoked_at = now
        tokens = self._issue_tokens(user.id, family_id=record.family_id)
        await self.session.commit()
        return tokens

    async def change_password(
        self, user: User, current_password: str, new_password: str
    ) -> IssuedTokens:
        """Set a new password and sign out every other session.

        All of the user's refresh tokens are revoked and a fresh session is issued for
        the caller. Other devices can't renew their access; their already-issued access
        tokens lapse within ``access_token_expire_minutes``.

        Wrong current passwords count toward a per-account limit, so a hijacked
        session can't be used to brute-force the password.
        """
        key = f"password-change:user:{user.id}"
        limit = {
            "limit": settings.login_account_failure_limit,
            "window_seconds": settings.login_account_window_seconds,
        }
        lockout = await self.rate_limiter.check(key, **limit)
        if not lockout.allowed:
            raise TooManyRequestsError(
                "Too many incorrect password attempts. Try again later.",
                retry_after=lockout.retry_after,
            )
        if not await to_thread.run_sync(verify_password, current_password, user.password_hash):
            await self.rate_limiter.hit(key, **limit)
            # 400, not 401: the caller *is* authenticated, and a 401 would make
            # clients treat their session as expired.
            raise AppError(WRONG_CURRENT_PASSWORD, code="invalid_current_password")
        if current_password == new_password:
            raise AppError(PASSWORD_UNCHANGED, code="password_unchanged")

        await self.rate_limiter.reset(key)
        user.password_hash = await to_thread.run_sync(hash_password, new_password)
        await self.refresh_tokens.revoke_all_for_user(user.id, utcnow())
        tokens = self._issue_tokens(user.id, family_id=uuid.uuid4())
        await self.session.commit()
        return tokens

    async def logout(self, raw_token: str | None) -> None:
        """Revoke the login session the refresh token belongs to. Idempotent."""
        if not raw_token:
            return
        record = await self.refresh_tokens.get_by_hash(hash_token(raw_token))
        if record is not None:
            await self.refresh_tokens.revoke_family(record.family_id, utcnow())
            await self.session.commit()

    async def _throttle(self, key: str, *, limit: int, window_seconds: int, message: str) -> None:
        result = await self.rate_limiter.hit(key, limit=limit, window_seconds=window_seconds)
        if not result.allowed:
            raise TooManyRequestsError(message, retry_after=result.retry_after)

    def _issue_tokens(self, user_id: uuid.UUID, *, family_id: uuid.UUID) -> IssuedTokens:
        raw_refresh = generate_token()
        refresh_expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
        self.refresh_tokens.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                token_hash=hash_token(raw_refresh),
                expires_at=refresh_expires_at,
            )
        )
        access_token, expires_in = create_access_token(user_id)
        return IssuedTokens(access_token, expires_in, raw_refresh, refresh_expires_at)
