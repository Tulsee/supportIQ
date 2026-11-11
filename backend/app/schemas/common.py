"""Schemas shared across endpoints."""

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
