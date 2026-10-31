"""Contratos V5 §15: lote, sequência por dispositivo e relógio com fuso."""

from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from rotalog.core.contracts import Input
from rotalog.core.data_mode import is_synthetic_data


class TrackingPoint(Input):
    sequence_no: Annotated[int, Field(strict=True, ge=0, le=9223372036854775807)]
    captured_at_device: AwareDatetime
    latitude: Annotated[Decimal, Field(ge=-90, le=90, max_digits=24, decimal_places=20)]
    longitude: Annotated[
        Decimal, Field(ge=-180, le=180, max_digits=24, decimal_places=20)
    ]
    accuracy_meters: Annotated[
        Decimal, Field(ge=0, le=10000, max_digits=24, decimal_places=20)
    ]
    speed_mps: Annotated[Decimal, Field(ge=0, le=1000)] | None = None
    bearing_degrees: Annotated[Decimal, Field(ge=0, lt=360)] | None = None
    is_mocked_by_os: bool = False

    @model_validator(mode="after")
    def storage_precision(self):
        # Android fornece doubles: normalizar evita exigir arredondamento no app.
        self.latitude = self.latitude.quantize(Decimal("0.000001"))
        self.longitude = self.longitude.quantize(Decimal("0.000001"))
        self.accuracy_meters = self.accuracy_meters.quantize(Decimal("0.01"))
        if self.speed_mps is not None:
            self.speed_mps = self.speed_mps.quantize(Decimal("0.000001"))
        if self.bearing_degrees is not None:
            self.bearing_degrees = self.bearing_degrees.quantize(
                Decimal("0.000001"), rounding=ROUND_DOWN
            )
        return self


class TrackingBatch(Input):
    client_batch_id: UUID
    device_id: Annotated[str, Field(min_length=3, max_length=100)]
    route_id: UUID | None = None
    driver_id: UUID | None = None
    points: list[TrackingPoint] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len({p.sequence_no for p in self.points}) != len(self.points):
            raise ValueError("Sequências não podem se repetir dentro do lote")
        return self


class TrackingStatusChange(Input):
    status: Literal["ACTIVE", "DEGRADED_OFFLINE", "DEGRADED_PERMISSION"]


class PointResponse(BaseModel):
    id: UUID
    device_id: str
    sequence_no: int
    captured_at_device: datetime
    received_at_server: datetime
    latitude: str
    longitude: str
    accuracy_meters: str
    speed_mps: str | None
    bearing_degrees: str | None
    is_mocked_by_os: bool
    flags: list[str]


class BatchPointResult(BaseModel):
    sequence_no: int
    result: Literal["STORED", "DUPLICATE", "REJECTED"]
    point_id: UUID | None = None
    flags: list[str] = Field(default_factory=list)
    reason: str | None = None


class BatchResponse(BaseModel):
    client_batch_id: UUID
    route_id: UUID
    results: list[BatchPointResult]
    operational_version: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class TrackingCurrent(BaseModel):
    route_id: UUID
    state: Literal[
        "NOT_STARTED",
        "ACTIVE",
        "DEGRADED_OFFLINE",
        "DEGRADED_PERMISSION",
        "STALE",
        "ENDED",
    ]
    is_stale: bool
    position: PointResponse | None
    operational_version: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class TrackingHistory(BaseModel):
    route_id: UUID
    items: list[PointResponse]
    next_cursor: str | None
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
