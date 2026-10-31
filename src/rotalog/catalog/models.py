"""Produtos-variante e ofertas; dinheiro persistido exclusivamente em centavos."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rotalog.core.database import Base, TimestampMixin, UuidPrimaryKeyMixin


class Category(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "category"
    __table_args__ = (UniqueConstraint("demo_run_id", "code"),)
    demo_run_id: Mapped[UUID] = mapped_column(ForeignKey("demo_run.id"))
    code: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))


class Product(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product"
    __table_args__ = (
        CheckConstraint(
            "publication_state IN ('DRAFT', 'PUBLISHED', 'UNPUBLISHED')",
            name="publication_state_valid",
        ),
        CheckConstraint(
            "net_content IS NULL OR net_content > 0", name="net_content_positive"
        ),
        CheckConstraint(
            "packaging_quantity IS NULL OR packaging_quantity > 0",
            name="packaging_quantity_positive",
        ),
        CheckConstraint(
            "gross_weight_grams IS NULL OR gross_weight_grams > 0",
            name="weight_positive",
        ),
        CheckConstraint("volume_cm3 IS NULL OR volume_cm3 > 0", name="volume_positive"),
    )
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    gtin: Mapped[str | None] = mapped_column(String(14))
    net_content: Mapped[int | None] = mapped_column(Integer)
    net_content_unit: Mapped[str | None] = mapped_column(String(10))
    packaging_type: Mapped[str | None] = mapped_column(String(100))
    packaging_quantity: Mapped[int | None] = mapped_column(Integer)
    gross_weight_grams: Mapped[int | None] = mapped_column(Integer)
    volume_cm3: Mapped[int | None] = mapped_column(Integer)
    variant_attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    publication_state: Mapped[str] = mapped_column(
        String(20), default="DRAFT", server_default="DRAFT"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    demo_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("demo_run.id", ondelete="RESTRICT"), index=True
    )


class Offer(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "offer"
    __table_args__ = (
        CheckConstraint(
            "minimum_order_quantity > 0", name="minimum_order_quantity_positive"
        ),
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        CheckConstraint("currency = 'BRL'", name="currency_brl"),
    )
    product_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("product.id", ondelete="RESTRICT"), index=True
    )
    supplier_organization_id: Mapped[UUID] = mapped_column(Uuid(), index=True)
    warehouse_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("warehouse.id", ondelete="RESTRICT")
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3), default="BRL", server_default="BRL")
    minimum_order_quantity: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    demo_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("demo_run.id", ondelete="RESTRICT"), index=True
    )


class OfferPriceVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "offer_price_version"
    __table_args__ = (
        UniqueConstraint("offer_id", "version"),
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        CheckConstraint(
            "minimum_order_quantity > 0", name="minimum_order_quantity_positive"
        ),
    )
    offer_id: Mapped[UUID] = mapped_column(ForeignKey("offer.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3), default="BRL", server_default="BRL")
    minimum_order_quantity: Mapped[int] = mapped_column(Integer)


class ProductImage(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_image"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        Index(
            "uq_product_image_live_file",
            "product_id",
            "file_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index(
            "uq_product_image_live_primary",
            "product_id",
            unique=True,
            postgresql_where=text("is_primary AND removed_at IS NULL"),
        ),
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT"), index=True
    )
    file_id: Mapped[UUID] = mapped_column(
        ForeignKey("stored_file.id", ondelete="RESTRICT")
    )
    position: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    alt_text: Mapped[str] = mapped_column(String(200))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductImageEvent(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_image_event"
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_user.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSONB)
