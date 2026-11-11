"""Application settings.

Values resolve in increasing priority from: the defaults below, the repository-root
``.env``, ``backend/.env``, then real environment variables. Field names map to
upper-case variables, e.g. ``database_url`` <- ``DATABASE_URL``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


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
    database_url: str = "postgresql+asyncpg://supportiq:supportiq@localhost:5432/supportiq"
    database_echo: bool = False
    database_health_timeout_seconds: float = 3.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
