"""Modelos persistentes do catálogo e do estoque inicial."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from rotalog.core.database import Base, TimestampMixin, UuidPrimaryKeyMixin

MONEY_PRECISION = 14
MONEY_SCALE = 2


class Product(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Produto canônico apresentado no marketplace."""

    __tablename__ = "product"

    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Offer(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Preço e condição comercial de um produto por fornecedor."""

    __tablename__ = "offer"
    __table_args__ = (
        CheckConstraint(
            "minimum_order_quantity > 0",
            name="minimum_order_quantity_positive",
        ),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    product_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("product.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    supplier_organization_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )
    minimum_order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)


class InventoryBalance(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Saldo declarado e reservado de uma oferta."""

    __tablename__ = "inventory_balance"
    __table_args__ = (
        CheckConstraint(
            "available_quantity >= 0",
            name="available_quantity_non_negative",
        ),
        CheckConstraint(
            "reserved_quantity >= 0",
            name="reserved_quantity_non_negative",
        ),
        CheckConstraint(
            "reserved_quantity <= available_quantity",
            name="reserved_not_greater_than_available",
        ),
    )

    offer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("offer.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
