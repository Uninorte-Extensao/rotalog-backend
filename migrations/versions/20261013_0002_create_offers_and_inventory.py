"""Cria ofertas e saldos de estoque com constraints.

Revision ID: 20261013_0002
Revises: 20261013_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0002"
down_revision: str | None = "20261013_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria as tabelas transacionais iniciais do marketplace."""

    op.create_table(
        "offer",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_organization_id", sa.Uuid(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("minimum_order_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "minimum_order_quantity > 0",
            name=op.f("ck_offer_minimum_order_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_offer_unit_price_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["product.id"],
            name=op.f("fk_offer_product_id_product"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offer")),
    )
    op.create_index(
        op.f("ix_offer_product_id"),
        "offer",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_offer_supplier_organization_id"),
        "offer",
        ["supplier_organization_id"],
        unique=False,
    )

    op.create_table(
        "inventory_balance",
        sa.Column("offer_id", sa.Uuid(), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "reserved_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "available_quantity >= 0",
            name=op.f("ck_inventory_balance_available_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name=op.f("ck_inventory_balance_reserved_quantity_non_negative"),
        ),
        sa.CheckConstraint(
            "reserved_quantity <= available_quantity",
            name=op.f("ck_inventory_balance_reserved_not_greater_than_available"),
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offer.id"],
            name=op.f("fk_inventory_balance_offer_id_offer"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_balance")),
        sa.UniqueConstraint(
            "offer_id",
            name=op.f("uq_inventory_balance_offer_id"),
        ),
    )


def downgrade() -> None:
    """Remove saldos e ofertas."""

    op.drop_table("inventory_balance")
    op.drop_index(op.f("ix_offer_supplier_organization_id"), table_name="offer")
    op.drop_index(op.f("ix_offer_product_id"), table_name="offer")
    op.drop_table("offer")
