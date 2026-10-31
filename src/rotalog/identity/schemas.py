"""Schemas de entrada e saída do módulo de identidade."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

from rotalog.identity.models import Role

NonBlankName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(
        min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class RegisterRequest(EmailInput):
    """Autocadastro de comprador/fornecedor em organização própria."""

    password: SecretStr = Field(min_length=12, max_length=1024)
    name: NonBlankName
    organization_name: NonBlankName
    role: Literal["BUYER_ADMIN", "SUPPLIER_OPERATOR"]


class DriverProvisionRequest(EmailInput):
    password: SecretStr = Field(min_length=12, max_length=1024)
    name: NonBlankName


class UserProvisionRequest(DriverProvisionRequest):
    organization_id: UUID
    role: Literal["ROTALOG_ADMIN", "ROUTE_PLANNER"]


class InvitationCreate(EmailInput):
    organization_id: UUID
    role: Literal["ROTALOG_ADMIN", "ROUTE_PLANNER"]
    expires_in_hours: int = Field(default=48, strict=True, ge=1, le=168)


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=32, max_length=256)
    name: NonBlankName
    password: SecretStr = Field(min_length=12, max_length=1024)


class InvitationResponse(BaseModel):
    id: UUID
    email: str
    role: Role
    organization_id: UUID
    expires_at: datetime
    invitation_token: str = Field(repr=False)


class OrganizationAccess(BaseModel):
    id: UUID
    name: str
    role: Role


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    role: Role
    organization_id: UUID
    organizations: list[OrganizationAccess] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    demo_run_id: UUID
    session_id: UUID | None = Field(default=None, exclude=True)
    auth_version: int = Field(default=1, exclude=True)
    is_synthetic: bool = True
    data_is_synthetic: bool = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 600
    refresh_token: str | None = None
    csrf_token: str | None = None
    user: UserResponse


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: SecretStr | None = Field(default=None, min_length=32, max_length=256)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class SessionResponse(BaseModel):
    id: UUID
    client_type: Literal["native", "browser"]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    current: bool
