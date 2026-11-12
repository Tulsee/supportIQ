"""Platform administration (super admin) schemas."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models import Organization
from app.schemas.organizations import OrganizationRead, OrganizationSettings


class AdminUserUpdate(BaseModel):
    is_active: bool | None = Field(
        default=None, description="Deactivating also signs the user out everywhere."
    )
    is_superadmin: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if self.is_active is None and self.is_superadmin is None:
            raise ValueError("Provide is_active and/or is_superadmin")
        return self


class AdminOrganizationRead(OrganizationRead):
    member_count: int

    @classmethod
    def build(cls, organization: Organization, member_count: int) -> Self:
        return cls(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            settings=OrganizationSettings.model_validate(organization.settings or {}),
            created_at=organization.created_at,
            member_count=member_count,
        )
