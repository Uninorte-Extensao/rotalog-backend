"""Execução canônica da demo; não converte rotas legadas em provas."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rotalog.core.database import Base, TimestampMixin, UuidPrimaryKeyMixin


class Vehicle(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle"
    __table_args__ = (
        CheckConstraint(
            "package_units > 0 AND weight_grams > 0 AND volume_cm3 > 0",
            name="capacity_positive",
        ),
        UniqueConstraint("demo_run_id", "registration"),
    )
    demo_run_id: Mapped[UUID] = mapped_column(ForeignKey("demo_run.id"))
    supplier_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization.id"), index=True
    )
    registration: Mapped[str] = mapped_column(String(30))
    package_units: Mapped[int] = mapped_column(Integer)
    weight_grams: Mapped[int] = mapped_column(Integer)
    volume_cm3: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )


class RoutePlanVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_plan_version"
    demo_run_id: Mapped[UUID] = mapped_column(ForeignKey("demo_run.id"))
    supplier_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organization.id"), index=True
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("identity_user.id"))
    state: Mapped[str] = mapped_column(
        String(32), default="PROPOSED", server_default="PROPOSED"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    snapshot: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(500))


class Route(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route"
    __table_args__ = (
        CheckConstraint(
            "state IN ('PUBLISHED','ACCEPTED','PICKUP_IN_PROGRESS','READY_TO_START',"
            "'STARTED','EXECUTION_ENDED','OPERATIONALLY_COMPLETED','CANCELLED')",
            name="state_valid",
        ),
    )
    plan_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("route_plan_version.id"), unique=True
    )
    warehouse_id: Mapped[UUID] = mapped_column(ForeignKey("warehouse.id"))
    demo_run_id: Mapped[UUID] = mapped_column(ForeignKey("demo_run.id"))
    state: Mapped[str] = mapped_column(
        String(32), default="PUBLISHED", server_default="PUBLISHED"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RouteAssignment(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_assignment"
    route_id: Mapped[UUID] = mapped_column(ForeignKey("route.id"), unique=True)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    vehicle_id: Mapped[UUID] = mapped_column(ForeignKey("vehicle.id"))
    assigned_by: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))


class RouteLoad(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_load"
    __table_args__ = (UniqueConstraint("route_id", "fulfillment_id"),)
    route_id: Mapped[UUID] = mapped_column(ForeignKey("route.id"))
    fulfillment_id: Mapped[UUID] = mapped_column(ForeignKey("fulfillment.id"))


class RouteStop(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_stop"
    __table_args__ = (
        UniqueConstraint("route_id", "sequence"),
        CheckConstraint(
            "sequence >= 0 AND kind IN ('PICKUP','DELIVERY')",
            name="sequence_kind_valid",
        ),
    )
    route_id: Mapped[UUID] = mapped_column(ForeignKey("route.id"))
    supplier_order_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("supplier_order.id")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    address_snapshot: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(
        String(32), default="PENDING", server_default="PENDING"
    )


class DeliveryAttempt(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_attempt"
    route_stop_id: Mapped[UUID] = mapped_column(
        ForeignKey("route_stop.id"), unique=True
    )
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    device_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(
        String(32), default="STARTED", server_default="STARTED"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    receiver_id: Mapped[UUID | None] = mapped_column(ForeignKey("identity_user.id"))
    receiver_name: Mapped[str | None] = mapped_column(String(200))
    receiver_role: Mapped[str | None] = mapped_column(String(200))
    authorization_hash: Mapped[str | None] = mapped_column(String(64))
    authorization_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    authorization_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    authorization_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )


class PrivateEvidence(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Adapter privado PostgreSQL para a demo; sem URL pública ou paths do cliente."""

    __tablename__ = "private_evidence"
    __table_args__ = (
        CheckConstraint("kind IN ('PHOTO','SIGNATURE')", name="kind_valid"),
    )
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_attempt.id"))
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    kind: Mapped[str] = mapped_column(String(20))
    mime_type: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("stored_file.id"), unique=True
    )


class DeliveryProof(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_proof"
    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("delivery_attempt.id"), unique=True
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    validation_state: Mapped[str] = mapped_column(String(32))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contestation_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class DeliveryOutcomeLine(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_outcome_line"
    __table_args__ = (
        UniqueConstraint("attempt_id", "fulfillment_line_id"),
        CheckConstraint(
            "buyer_accepted_quantity >= 0 AND delivery_refused_quantity >= 0 AND missing_quantity >= 0",
            name="quantities_non_negative",
        ),
    )
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_attempt.id"))
    fulfillment_line_id: Mapped[UUID] = mapped_column(ForeignKey("fulfillment_line.id"))
    buyer_accepted_quantity: Mapped[int] = mapped_column(Integer)
    delivery_refused_quantity: Mapped[int] = mapped_column(Integer)
    missing_quantity: Mapped[int] = mapped_column(Integer)


class DeliveryProofLineEvidence(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "delivery_proof_line_evidence"
    __table_args__ = (UniqueConstraint("outcome_line_id", "evidence_id"),)
    outcome_line_id: Mapped[UUID] = mapped_column(
        ForeignKey("delivery_outcome_line.id")
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("private_evidence.id"))


class CustodyEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "custody_event"
    route_id: Mapped[UUID] = mapped_column(ForeignKey("route.id"))
    fulfillment_line_id: Mapped[UUID] = mapped_column(ForeignKey("fulfillment_line.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    kind: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)


class OrderEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Timeline operacional e auditoria transacional; não é event sourcing."""

    __tablename__ = "order_event"
    supplier_order_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplier_order.id"), index=True
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    kind: Mapped[str] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSONB)
