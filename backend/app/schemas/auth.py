from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.users import FullName, NormalizedEmail


class RegisterRequest(BaseModel):
    email: NormalizedEmail
    password: Annotated[str, Field(min_length=8, max_length=128)]
    full_name: FullName


class LoginRequest(BaseModel):
    email: NormalizedEmail
    # No min length here: the password policy is not revealed at login.
    password: Annotated[str, Field(min_length=1, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")
