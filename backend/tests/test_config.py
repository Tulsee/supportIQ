import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG_SECRET = "x" * 48


def test_short_jwt_secret_is_rejected_in_every_environment() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None, environment="development", jwt_secret_key="")


def test_production_rejects_the_development_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="random value"):
        Settings(_env_file=None, environment="production", refresh_cookie_secure=True)


def test_production_requires_secure_refresh_cookie() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        Settings(_env_file=None, environment="production", jwt_secret_key=STRONG_SECRET)


def test_production_accepts_safe_settings() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key=STRONG_SECRET,
        refresh_cookie_secure=True,
    )

    assert settings.is_production
