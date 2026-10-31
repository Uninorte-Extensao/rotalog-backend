"""Contratos da rota manual e prova da demonstração V5."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from rotalog.core.contracts import (
    Input,
    Name,
    PositiveQuantity,
    Quantity,
    Reason,
    Versioned,
)
from rotalog.core.data_mode import is_synthetic_data
from rotalog.logistics.cargo_schemas import CargoSummary, ManifestLineResponse
from rotalog.logistics.tracking_schemas import TrackingCurrent

Latitude = Annotated[Decimal, Field(ge=-90, le=90, max_digits=9, decimal_places=6)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, max_digits=9, decimal_places=6)]


class VehicleCreate(Input):
    registration: Annotated[
        str, Field(min_length=3, max_length=30, pattern=r"^[A-Z0-9-]+$")
    ]
    package_units: PositiveQuantity
    weight_grams: PositiveQuantity
    volume_cm3: PositiveQuantity


class EligibleFulfillmentLine(BaseModel):
    id: UUID
    order_item_id: UUID
    separated_quantity: int
    product_snapshot: dict


class EligibleFulfillmentResponse(BaseModel):
    id: UUID
    supplier_order_id: UUID
    supplier_organization_id: UUID
    buyer_organization_id: UUID
    warehouse_id: UUID
    state: str
    version: int
    package_units: int
    weight_grams: int
    volume_cm3: int
    lines: list[EligibleFulfillmentLine]
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class Destination(Input):
    supplier_order_id: UUID
    address: Annotated[str, Field(min_length=5, max_length=500)]
    latitude: Latitude
    longitude: Longitude
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    time_zone: Literal["America/Manaus"] = "America/Manaus"
    service_seconds: PositiveQuantity = 300
    recipient_name: Name | None = None
    contact_name: Name | None = None
    contact_phone: Annotated[str, Field(max_length=50)] | None = None
    reference: Annotated[str, Field(max_length=500)] | None = None
    instructions: Annotated[str, Field(max_length=1000)] | None = None

    @model_validator(mode="after")
    def window(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Janela deve terminar após o início")
        return self


class ManualPlanCreate(Input):
    warehouse_id: UUID
    driver_id: UUID
    vehicle_id: UUID
    fulfillment_ids: list[UUID] = Field(min_length=1, max_length=30)
    deliveries: list[Destination] = Field(min_length=1, max_length=30)
    reason: Reason

    @model_validator(mode="after")
    def distinct(self):
        if len(set(self.fulfillment_ids)) != len(self.fulfillment_ids):
            raise ValueError("Lotes repetidos")
        ids = [item.supplier_order_id for item in self.deliveries]
        if len(set(ids)) != len(ids):
            raise ValueError("Uma parada por pedido nesta demo")
        return self


class AttemptCreate(Versioned):
    device_id: Annotated[str, Field(min_length=3, max_length=100)]


class ReceiverAuthorization(Input):
    receiver_role: Name


class OutcomeInput(Input):
    fulfillment_line_id: UUID
    buyer_accepted_quantity: Quantity
    delivery_refused_quantity: Quantity = 0


class ProofInput(Versioned):
    captured_at: AwareDatetime
    latitude: Latitude
    longitude: Longitude
    accuracy_meters: Annotated[
        Decimal, Field(ge=0, le=10000, max_digits=8, decimal_places=2)
    ]
    receiver_code: Annotated[str, Field(pattern=r"^[0-9]{6}$")] | None = Field(
        default=None, repr=False
    )
    signature_id: UUID | None = None
    photo_ids: list[UUID] = Field(default_factory=list, max_length=5)
    visible_condition: Name
    package_count: PositiveQuantity
    observations: Annotated[str, Field(max_length=1000)] = ""
    lines: list[OutcomeInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct(self):
        ids = [line.fulfillment_line_id for line in self.lines]
        if len(set(ids)) != len(ids) or len(set(self.photo_ids)) != len(self.photo_ids):
            raise ValueError("Linhas e fotos devem ser únicas")
        return self


class RouteSummary(BaseModel):
    id: UUID
    state: str
    version: int
    driver_id: UUID
    vehicle_id: UUID
    updated_at: datetime
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class StopResponse(BaseModel):
    id: UUID
    supplier_order_id: UUID | None
    sequence: int
    kind: str
    state: str
    address_snapshot: dict
    attempt_id: UUID | None
    attempt_state: str | None
    attempt_version: int | None
    items: list[ManifestLineResponse] = Field(default_factory=list)


class RouteResponse(RouteSummary):
    warehouse_id: UUID
    plan_version_id: UUID
    stops: list[StopResponse]
    code: str | None = None
    scheduled_date: str | None = None
    started_at: datetime | None = None
    execution_ended_at: datetime | None = None
    vehicle: dict | None = None
    cargo: CargoSummary | None = None
    tracking: TrackingCurrent | None = None
    allowed_actions: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    id: UUID
    state: str
    version: int
    snapshot: dict
    reason: str
    supplier_organization_id: UUID | None = None
    is_synthetic: bool = Field(default_factory=is_synthetic_data)


class EvidenceResponse(BaseModel):
    id: UUID
    kind: str
    mime_type: str
    sha256: str
    download_path: str


class AttemptResponse(BaseModel):
    id: UUID
    route_stop_id: UUID
    state: str
    version: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
