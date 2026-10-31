from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rotalog.core.contracts import (
    Input,
    Name,
    PositiveQuantity,
    Quantity,
    Reason,
    Versioned,
)
from rotalog.core.data_mode import is_synthetic_data


class WarehouseCreate(Input):
    name: Name
    address: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)
    ]
    opening_hours: Name
    region: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class WarehouseResponse(WarehouseCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    supplier_organization_id: UUID
    geocoding_state: str
    latitude: Decimal | None
    longitude: Decimal | None
    is_active: bool
    version: int
    demo_run_id: UUID
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class GeocodingReview(Versioned):
    latitude: Decimal = Field(
        ge=-90, le=90, max_digits=9, decimal_places=6, allow_inf_nan=False
    )
    longitude: Decimal = Field(
        ge=-180, le=180, max_digits=9, decimal_places=6, allow_inf_nan=False
    )
    reason: Reason


class InventoryAdjustmentRequest(Versioned):
    offer_id: UUID
    warehouse_id: UUID
    external_sellable_quantity: Quantity
    safety_stock_quantity: Quantity = 0
    reason: Reason


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    offer_id: UUID
    product_id: UUID | None = None
    product_name: str | None = None
    offer_active: bool | None = None
    warehouse_id: UUID
    external_physical_quantity: int | None
    external_sellable_quantity: int
    rotalog_reserved_not_reflected_quantity: int
    safety_stock_quantity: int
    effective_sellable_quantity: int
    version: int
    source: str
    external_version: str | None
    freshness_state: str
    source_observed_at: datetime
    received_at: datetime
    last_reconciled_at: datetime | None
    demo_run_id: UUID
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class CapacityCreate(Input):
    warehouse_id: UUID
    starts_at: datetime
    ends_at: datetime
    total_quantity: PositiveQuantity

    @model_validator(mode="after")
    def valid_window(self) -> "CapacityCreate":
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
            or self.ends_at <= self.starts_at
        ):
            raise ValueError("Janela deve ter fuso e fim posterior ao início")
        return self


class CapacityAdjustment(Versioned):
    total_quantity: Quantity
    reason: Reason


class CapacityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    warehouse_id: UUID
    starts_at: datetime
    ends_at: datetime
    total_quantity: int
    reserved_quantity: int
    version: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
