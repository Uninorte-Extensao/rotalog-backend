"""Contratos HTTP do rastreamento de entregas."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rotalog.core.contracts import Input, Versioned
from rotalog.core.data_mode import is_synthetic_data


class DeliveryRouteCreate(Versioned):
    """Atribui um motorista a um pedido pronto para despacho."""

    driver_id: UUID


class LocationCreate(Input):
    """Ponto GPS gerado pelo dispositivo do motorista."""

    latitude: Annotated[Decimal, Field(ge=-90, le=90, max_digits=8, decimal_places=6)]
    longitude: Annotated[
        Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    ]
    accuracy_meters: Annotated[
        Decimal, Field(ge=0, le=10_000, max_digits=8, decimal_places=2)
    ]
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at deve incluir fuso horário")
        return value


class DeliveryRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    supplier_order_id: UUID
    driver_id: UUID
    state: str
    version: int
    last_latitude: Decimal | None
    last_longitude: Decimal | None
    last_accuracy_meters: Decimal | None
    last_location_at: datetime | None
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    route_id: UUID
    driver_id: UUID
    latitude: Decimal
    longitude: Decimal
    accuracy_meters: Decimal
    recorded_at: datetime
    created_at: datetime
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class LastLocationResponse(BaseModel):
    id: UUID
    route_id: UUID
    driver_id: UUID
    latitude: Decimal
    longitude: Decimal
    accuracy_meters: Decimal
    recorded_at: datetime
    is_stale: bool
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
