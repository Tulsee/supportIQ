"""Password hashing, JWT access tokens, and opaque refresh tokens.

- Passwords: argon2id via pwdlib.
- Access tokens: short-lived HS256 JWTs carrying only the user id. Organization
  and role are resolved per request from the database, so membership changes
  take effect immediately instead of when the token expires.
- Refresh and invitation tokens: random opaque strings. Only their SHA-256 hash
  is stored, so a database leak does not yield usable tokens. (A fast hash is fine
  here: unlike passwords, the tokens have 384 bits of entropy.)
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import cache

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

_password_hash = PasswordHash.recommended()


class InvalidTokenError(Exception):
    """The access token is missing claims, malformed, expired, or wrongly signed."""


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- Passwords -------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


@cache
def _dummy_hash() -> str:
    return _password_hash.hash("timing-equalization-dummy-password")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verify a password. Pass ``None`` for unknown users: a dummy hash is still
    checked so response time does not reveal whether an email is registered."""
    if password_hash is None:
        _password_hash.verify(password, _dummy_hash())
        return False
    return _password_hash.verify(password, password_hash)


# --- Access tokens (JWT) ------------------------------------------------------------


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    expires_in = settings.access_token_expire_minutes * 60
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm=JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> uuid.UUID:
    """Return the user id from a valid access token, else raise ``InvalidTokenError``."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "type", "iat", "exp"]},
        )
        if payload["type"] != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("Not an access token")
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


# --- Opaque tokens (refresh tokens, invitation tokens) ------------------------------


def generate_token() -> str:
    """384 bits of randomness, URL-safe (64 characters)."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """What gets stored. A fast hash is enough for high-entropy random tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
