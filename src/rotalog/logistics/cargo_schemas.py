"""Extensões do contrato V5 para conferência global e ocorrências pré-coleta."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from rotalog.core.contracts import Quantity, Reason, Versioned
from rotalog.core.data_mode import is_synthetic_data


class ManifestCommand(Versioned):
    manifest_version: int = Field(strict=True, ge=1)


class GlobalDeclaration(ManifestCommand):
    declaration: Literal[True]

    @field_validator("declaration", mode="before")
    @classmethod
    def affirmative(cls, value):
        if value is not True:
            raise ValueError("A declaração deve ser explicitamente true")
        return value


class CargoCheck(GlobalDeclaration):
    checked_line_ids: list[UUID] = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.checked_line_ids)) != len(self.checked_line_ids):
            raise ValueError("Linhas conferidas não podem se repetir")
        return self


class CargoReceipt(GlobalDeclaration):
    pass


class CargoIssueCreate(ManifestCommand):
    fulfillment_line_id: UUID
    expected_quantity: Quantity
    observed_quantity: Quantity
    category: Literal["MISSING", "EXCESS", "DAMAGED", "WRONG_PRODUCT", "OTHER"]
    reason: Reason
    photo_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.photo_ids)) != len(self.photo_ids):
            raise ValueError("Fotos não podem se repetir")
        if (
            self.category == "MISSING"
            and self.observed_quantity >= self.expected_quantity
        ):
            raise ValueError("Falta exige quantidade encontrada menor que a esperada")
        if (
            self.category == "EXCESS"
            and self.observed_quantity <= self.expected_quantity
        ):
            raise ValueError("Sobra exige quantidade encontrada maior que a esperada")
        return self


class CargoIssueResolve(ManifestCommand):
    correction: Reason


class ManifestLineResponse(BaseModel):
    id: UUID
    fulfillment_line_id: UUID
    fulfillment_id: UUID
    order_item_id: UUID
    supplier_order_id: UUID
    product_id: UUID
    product_name: str
    brand: str | None
    packaging_type: str | None
    packaging_quantity: int | None
    net_content: int | None
    net_content_unit: str | None
    quantity_unit: Literal["SELLABLE_PACKAGE"]
    expected_quantity: int
    dispatched_quantity: int


class CargoIssueResponse(BaseModel):
    id: UUID
    fulfillment_line_id: UUID
    expected_quantity: int
    observed_quantity: int
    category: str
    reason: str
    state: Literal["OPEN", "RESOLVED"]
    correction: str | None
    reported_by: UUID
    resolved_by: UUID | None
    created_at: datetime
    resolved_at: datetime | None
    photo_ids: list[UUID]


class CargoSummary(BaseModel):
    route_id: UUID
    route_version: int
    manifest_version: int
    fingerprint: str
    inspection_required: bool
    requires_replanning: bool
    declaration_valid: bool
    checked_at: datetime | None
    received_at: datetime | None
    read_only: bool
    unresolved_issues: int
    total_sellable_packages: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class CargoManifest(CargoSummary):
    lines: list[ManifestLineResponse]
    issues: list[CargoIssueResponse]
