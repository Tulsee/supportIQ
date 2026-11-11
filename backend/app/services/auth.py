"""Registration, login, and refresh-token rotation."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from anyio import to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    utcnow,
    verify_password,
)
from app.models import RefreshToken, User
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.auth import RegisterRequest

INVALID_CREDENTIALS = "Invalid email or password"
EMAIL_TAKEN = "An account with this email already exists"


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register(self, data: RegisterRequest) -> User:
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

    async def login(self, email: str, password: str) -> IssuedTokens:
        user = await self.users.get_by_email(email)
        password_ok = await to_thread.run_sync(
            verify_password, password, user.password_hash if user else None
        )
        # One message for every failure, so responses don't reveal which emails exist.
        if user is None or not password_ok or not user.is_active:
            raise AuthenticationError(INVALID_CREDENTIALS)
        tokens = self._issue_tokens(user.id, family_id=uuid.uuid4())
        await self.session.commit()
        return tokens

    async def refresh(self, raw_token: str | None) -> IssuedTokens:
        """Exchange a refresh token for a new access token and a new refresh token."""
        if not raw_token:
            raise AuthenticationError("Missing refresh token")
        now = utcnow()
        record = await self.refresh_tokens.get_by_hash(
            hash_refresh_token(raw_token), for_update=True
        )
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

    async def logout(self, raw_token: str | None) -> None:
        """Revoke the login session the refresh token belongs to. Idempotent."""
        if not raw_token:
            return
        record = await self.refresh_tokens.get_by_hash(hash_refresh_token(raw_token))
        if record is not None:
            await self.refresh_tokens.revoke_family(record.family_id, utcnow())
            await self.session.commit()

    def _issue_tokens(self, user_id: uuid.UUID, *, family_id: uuid.UUID) -> IssuedTokens:
        raw_refresh = generate_refresh_token()
        refresh_expires_at = utcnow() + timedelta(days=settings.refresh_token_expire_days)
        self.refresh_tokens.add(
            RefreshToken(
                user_id=user_id,
                family_id=family_id,
                token_hash=hash_refresh_token(raw_refresh),
                expires_at=refresh_expires_at,
            )
        )
        access_token, expires_in = create_access_token(user_id)
        return IssuedTokens(access_token, expires_in, raw_refresh, refresh_expires_at)
