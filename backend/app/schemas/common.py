"""Schemas shared across endpoints."""

from http import HTTPStatus
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable, machine-readable error code, e.g. `not_found`.")
    message: str = Field(description="Human-readable summary that is safe to show to users.")
    details: Any | None = Field(
        default=None, description="Optional structured context, e.g. field validation errors."
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Page[T](BaseModel):
    """Envelope for paginated list endpoints."""

    items: list[T]
    total: int = Field(description="Total matching items across all pages.")
    limit: int
    offset: int


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries documenting the error envelope for these codes.

    Including 422 also replaces FastAPI's default validation-error schema, which
    does not match the envelope this API actually returns.
    """
    return {
        code: {"model": ErrorResponse, "description": HTTPStatus(code).phrase}
        for code in status_codes
    }
