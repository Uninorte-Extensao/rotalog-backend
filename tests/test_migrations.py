"""Testes de integração das migrações em PostgreSQL."""

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_SNAPSHOT = "20261013_0001"


@pytest.fixture
def database_url() -> str:
    url = os.getenv("ROTALOG_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("ROTALOG_TEST_DATABASE_URL não configurada")
    return url


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _reset_database(database_url: str) -> None:
    engine = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture
def clean_database(database_url: str) -> Iterator[None]:
    _reset_database(database_url)
    yield
    _reset_database(database_url)


def test_migrations_run_on_empty_database(
    alembic_config: Config,
    database_url: str,
    clean_database: None,
) -> None:
    command.upgrade(alembic_config, "head")

    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        assert {"product", "offer", "inventory_balance"} <= set(
            inspector.get_table_names()
        )

        offer_columns = {column["name"]: column for column in inspector.get_columns("offer")}
        price_type = offer_columns["unit_price"]["type"]
        assert isinstance(price_type, sa.Numeric)
        assert (price_type.precision, price_type.scale) == (14, 2)

        product_columns = {
            column["name"]: column for column in inspector.get_columns("product")
        }
        assert product_columns["created_at"]["type"].timezone is True
    finally:
        engine.dispose()

    command.downgrade(alembic_config, "base")
    engine = sa.create_engine(database_url)
    try:
        assert not {
            "product",
            "offer",
            "inventory_balance",
        } & set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migrations_preserve_previous_snapshot(
    alembic_config: Config,
    database_url: str,
    clean_database: None,
) -> None:
    command.upgrade(alembic_config, PREVIOUS_SNAPSHOT)
    product_id = uuid4()
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("INSERT INTO product (id, name) VALUES (:id, :name)"),
                {"id": product_id, "name": "Produto existente"},
            )
    finally:
        engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                sa.text("SELECT name FROM product WHERE id = :id"),
                {"id": product_id},
            ) == "Produto existente"
        assert {"offer", "inventory_balance"} <= set(
            sa.inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_database_constraints_reject_invalid_values(
    alembic_config: Config,
    database_url: str,
    clean_database: None,
) -> None:
    command.upgrade(alembic_config, "head")
    engine = sa.create_engine(database_url)
    product_id = uuid4()
    supplier_id = uuid4()

    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("INSERT INTO product (id, name) VALUES (:id, :name)"),
                {"id": product_id, "name": "Produto válido"},
            )

        invalid_offers = (
            {"unit_price": Decimal("-0.01"), "minimum_order_quantity": 1},
            {"unit_price": Decimal("10.00"), "minimum_order_quantity": 0},
        )
        for invalid_values in invalid_offers:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO offer (
                            id, product_id, supplier_organization_id,
                            unit_price, minimum_order_quantity
                        ) VALUES (
                            :id, :product_id, :supplier_id,
                            :unit_price, :minimum_order_quantity
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "product_id": product_id,
                        "supplier_id": supplier_id,
                        **invalid_values,
                    },
                )

        offer_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO offer (
                        id, product_id, supplier_organization_id,
                        unit_price, minimum_order_quantity
                    ) VALUES (:id, :product_id, :supplier_id, 10.00, 1)
                    """
                ),
                {
                    "id": offer_id,
                    "product_id": product_id,
                    "supplier_id": supplier_id,
                },
            )

        invalid_balances = ((-1, 0), (1, -1), (1, 2))
        for available_quantity, reserved_quantity in invalid_balances:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO inventory_balance (
                            id, offer_id, available_quantity, reserved_quantity
                        ) VALUES (
                            :id, :offer_id, :available_quantity, :reserved_quantity
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "offer_id": offer_id,
                        "available_quantity": available_quantity,
                        "reserved_quantity": reserved_quantity,
                    },
                )
    finally:
        engine.dispose()
