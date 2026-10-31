from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from rotalog.catalog.schemas import ProductResponse
from rotalog.core.contracts import Input, PositiveQuantity, Quantity, Reason, Versioned
from rotalog.logistics.delivery_schemas import RouteSummary


class CheckoutItem(Input):
    offer_id: UUID
    quantity: PositiveQuantity


class CheckoutCreate(Input):
    items: list[CheckoutItem] = Field(min_length=1, max_length=50)
    capacity_slot_id: UUID
    accepts_partial: Literal[False] = False

    @model_validator(mode="after")
    def distinct_offers(self) -> "CheckoutCreate":
        if len({item.offer_id for item in self.items}) != len(self.items):
            raise ValueError("Cada oferta deve aparecer uma única vez")
        return self


class AcceptanceLine(Input):
    order_item_id: UUID
    accepted_quantity: Quantity


class Acceptance(Versioned):
    lines: list[AcceptanceLine] = Field(min_length=1, max_length=50)


class FulfillmentItem(Input):
    order_item_id: UUID
    allocated_quantity: PositiveQuantity


class FulfillmentCreate(Versioned):
    lines: list[FulfillmentItem] = Field(min_length=1, max_length=50)


class SeparationLine(Input):
    fulfillment_line_id: UUID
    separated_quantity: Quantity


class SeparationResults(Versioned):
    lines: list[SeparationLine] = Field(min_length=1, max_length=50)


class Cancellation(Versioned):
    reason: Reason


class CheckoutLineResponse(BaseModel):
    offer_id: UUID
    quantity: int
    unit_price: str
    currency: Literal["BRL"]
    product: ProductResponse


class CheckoutResponse(BaseModel):
    id: UUID
    state: str
    version: int
    supplier_organization_id: UUID
    warehouse_id: UUID
    capacity_slot_id: UUID
    expires_at: datetime
    accepts_partial: bool
    currency: Literal["BRL"]
    total: str
    items: list[CheckoutLineResponse]
    purchase_id: UUID | None
    is_synthetic: bool


class PaymentConfirmationResponse(BaseModel):
    purchase_id: UUID
    supplier_order_ids: list[UUID]
    payment_id: UUID
    payment_state: Literal["CAPTURED"]
    provider: Literal["SIMULATED"]
    total: str
    currency: Literal["BRL"]
    is_synthetic: bool


class FulfillmentLineResponse(BaseModel):
    id: UUID
    order_item_id: UUID
    allocated_quantity: int
    separated_quantity: int
    dispatched_quantity: int


class FulfillmentResponse(BaseModel):
    id: UUID
    supplier_order_id: UUID
    warehouse_id: UUID
    state: str
    version: int
    is_synthetic: bool
    lines: list[FulfillmentLineResponse]


class OrderItemResponse(BaseModel):
    id: UUID
    offer_id: UUID
    product: ProductResponse
    unit_price: str
    currency: Literal["BRL"]
    offer_version: int
    requested_quantity: int
    reserved_quantity: int
    supplier_accepted_quantity: int
    supplier_refused_quantity: int
    separated_quantity: int
    dispatched_quantity: int
    delivered_quantity: int
    buyer_accepted_quantity: int
    delivery_refused_quantity: int
    missing_quantity: int
    returning_quantity: int
    return_reconciled_quantity: int
    refunded_quantity: int
    financially_released_quantity: int
    financially_pending_quantity: int


class SupplierOrderResponse(BaseModel):
    id: UUID
    purchase_id: UUID
    buyer_organization_id: UUID
    supplier_organization_id: UUID
    warehouse_id: UUID
    state: str
    version: int
    accepts_partial: bool
    acceptance_deadline: datetime
    currency: Literal["BRL"]
    total: str
    cancellation_reason: str | None
    items: list[OrderItemResponse]
    allowed_actions: list[str]
    is_synthetic: bool
    fulfillments: list[FulfillmentResponse]
    created_at: datetime
    updated_at: datetime
    delivery_routes: list[RouteSummary] = Field(default_factory=list)


class PurchaseResponse(BaseModel):
    id: UUID
    checkout_id: UUID
    state: str
    supplier_orders: list[SupplierOrderResponse]
    is_synthetic: bool
