"""Registro explícito dos modelos para migrações e ferramentas de banco."""

from rotalog.catalog.models import (
    Category,
    Offer,
    OfferPriceVersion,
    Product,
    ProductImage,
    ProductImageEvent,
)
from rotalog.demo.models import DemoRun
from rotalog.finance.models import Payment, PaymentAllocation
from rotalog.identity.models import (
    AuthSession,
    LoginThrottle,
    RefreshToken,
    User,
    UserInvitation,
)
from rotalog.inventory.models import (
    CapacitySlot,
    InventoryAdjustment,
    InventoryProjection,
    Reservation,
    Warehouse,
)
from rotalog.logistics.cargo_models import (
    CargoEvent,
    CargoInspection,
    CargoIssue,
    CargoIssuePhoto,
)
from rotalog.logistics.delivery_models import (
    CustodyEvent,
    DeliveryAttempt,
    DeliveryOutcomeLine,
    DeliveryProof,
    DeliveryProofLineEvidence,
    OrderEvent,
    PrivateEvidence,
    Route,
    RouteAssignment,
    RouteLoad,
    RoutePlanVersion,
    RouteStop,
    Vehicle,
)
from rotalog.logistics.models import DeliveryRoute, DriverLocationPoint
from rotalog.logistics.tracking_models import (
    CurrentRoutePosition,
    LocationBatch,
    RouteLocationPoint,
)
from rotalog.operations.file_models import FileDownloadGrant, FileEvent, StoredFile
from rotalog.operations.models import IdempotencyRecord
from rotalog.ordering.models import (
    CheckoutLine,
    CheckoutSession,
    Fulfillment,
    FulfillmentLine,
    OrderItem,
    Purchase,
    SupplierOrder,
)
from rotalog.organizations.models import Membership, Organization

__all__ = [
    "AuthSession",
    "CapacitySlot",
    "CargoEvent",
    "CargoInspection",
    "CargoIssue",
    "CargoIssuePhoto",
    "Category",
    "CheckoutLine",
    "CheckoutSession",
    "CurrentRoutePosition",
    "CustodyEvent",
    "DeliveryAttempt",
    "DeliveryOutcomeLine",
    "DeliveryProof",
    "DeliveryProofLineEvidence",
    "DeliveryRoute",
    "DemoRun",
    "DriverLocationPoint",
    "FileDownloadGrant",
    "FileEvent",
    "Fulfillment",
    "FulfillmentLine",
    "IdempotencyRecord",
    "InventoryAdjustment",
    "InventoryProjection",
    "LocationBatch",
    "LoginThrottle",
    "Membership",
    "Offer",
    "OfferPriceVersion",
    "OrderEvent",
    "OrderItem",
    "Organization",
    "Payment",
    "PaymentAllocation",
    "PrivateEvidence",
    "Product",
    "ProductImage",
    "ProductImageEvent",
    "Purchase",
    "RefreshToken",
    "Reservation",
    "Route",
    "RouteAssignment",
    "RouteLoad",
    "RouteLocationPoint",
    "RoutePlanVersion",
    "RouteStop",
    "StoredFile",
    "SupplierOrder",
    "User",
    "UserInvitation",
    "Vehicle",
    "Warehouse",
]
