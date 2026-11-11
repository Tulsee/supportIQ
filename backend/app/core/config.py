"""Application settings.

Values resolve in increasing priority from: the defaults below, the repository-root
``.env``, ``backend/.env``, then real environment variables. Field names map to
upper-case variables, e.g. ``database_url`` <- ``DATABASE_URL``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# Development-only signing key. Rejected at startup outside development/test.
_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-in-env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ----------------------------------------------------------
    project_name: str = "SupportIQ"
    version: str = "0.1.0"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    # --- HTTP -----------------------------------------------------------------
    # Comma-separated in env: CORS_ORIGINS=http://localhost:3000,https://app.example.com
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Database -------------------------------------------------------------
    # Default matches docker-compose.yml (host port 5433). Use 127.0.0.1, not
    # "localhost": on Windows, localhost tries IPv6 first and each new connection
    # stalls ~2s before falling back to the IPv4-only Docker port binding.
    database_url: str = "postgresql+asyncpg://supportiq:supportiq@127.0.0.1:5433/supportiq"
    database_echo: bool = False
    database_health_timeout_seconds: float = 3.0

    # --- Auth -----------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    refresh_cookie_name: str = "supportiq_refresh"
    # Must be true whenever the API is served over HTTPS (enforced outside dev/test).
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _enforce_auth_safety(self) -> Self:
        secret = self.jwt_secret_key.get_secret_value()
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        if self.environment in ("development", "test"):
            return self
        if secret == _DEV_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a random value outside development/test"
            )
        if not self.refresh_cookie_secure:
            raise ValueError("REFRESH_COOKIE_SECURE must be true outside development/test")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def refresh_cookie_path(self) -> str:
        """The refresh cookie is only ever sent to the auth endpoints."""
        return f"{self.api_v1_prefix}/auth"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
