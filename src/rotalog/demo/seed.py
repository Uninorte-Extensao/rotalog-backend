"""Seed sintético explícito e idempotente; nunca sobrescreve senhas ou saldos."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from getpass import getpass
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

import rotalog.core.models  # noqa: F401
from rotalog.catalog.models import Category, Offer, OfferPriceVersion, Product
from rotalog.core.config import Settings, get_settings
from rotalog.core.database import (
    create_database_engine,
    create_session_factory,
    transactional_session,
)
from rotalog.core.money import minor
from rotalog.demo.fixtures import DEMO_NAME, DEMO_RUN_ID, PRODUCTS, USERS
from rotalog.demo.models import DemoRun
from rotalog.identity.models import Role, User
from rotalog.identity.security import password_hash, require_demo
from rotalog.inventory.models import CapacitySlot, InventoryProjection, Warehouse
from rotalog.organizations.models import Membership, Organization

ADMIN_ID = UUID("42359c32-0939-4c80-bb8d-1d601fe57ce4")
PLANNER_ID = UUID("62c215db-dc12-48f5-a974-ecac82accc24")
ROLES = {
    "comprador": Role.BUYER,
    "fornecedor": Role.SUPPLIER,
    "motorista": Role.DRIVER,
    "admin": Role.ADMIN,
    "planejador": Role.PLANNER,
}


def seed_demo(
    factory: sessionmaker[Session], settings: Settings, password: str
) -> None:
    require_demo(settings)
    if not 12 <= len(password) <= 1024:
        raise ValueError("A senha da demo deve ter entre 12 e 1024 caracteres")
    # Hashes fora dos locks; o seed só usa os hashes de usuários ainda ausentes.
    users = (
        *USERS,
        (ADMIN_ID, "admin", "Operação RotaLog Sintética"),
        (PLANNER_ID, "planejador", "Planejamento Sintético"),
    )
    hashes = {alias: password_hash.hash(password) for _, alias, _ in users}
    with transactional_session(factory) as session:
        session.execute(text("SELECT pg_advisory_xact_lock(20261018)"))
        run = session.get(DemoRun, DEMO_RUN_ID)
        if run is None:
            session.add(DemoRun(id=DEMO_RUN_ID, name=DEMO_NAME))
            session.flush()
        elif not run.is_active:
            raise ValueError("Seed não reabre execução encerrada")
        if not session.scalar(
            select(Category.id).where(
                Category.demo_run_id == DEMO_RUN_ID,
                Category.code == "ALIMENTO_SECO_INDUSTRIALIZADO",
            )
        ):
            session.add(
                Category(
                    demo_run_id=DEMO_RUN_ID,
                    code="ALIMENTO_SECO_INDUSTRIALIZADO",
                    name="Alimento seco industrializado",
                )
            )
        organization_ids = {}
        for user_id, alias, name in users:
            user = session.get(User, user_id)
            if user is None:
                organization = Organization(
                    id=uuid4(),
                    name=name,
                    demo_run_id=DEMO_RUN_ID,
                    kind="SUPPLIER"
                    if alias == "fornecedor"
                    else ("INTERNAL" if alias in {"admin", "planejador"} else "BUYER"),
                )
                session.add(organization)
                session.flush()
                user = User(
                    id=user_id,
                    email=f"{alias}@demo.rotalog.invalid",
                    name=f"{alias.capitalize()} Sintético",
                    role=ROLES[alias],
                    password_hash=hashes[alias],
                    organization_id=organization.id,
                    demo_run_id=DEMO_RUN_ID,
                )
                session.add(user)
                session.flush()
            organization_ids[alias] = user.organization_id
            if not session.scalar(
                select(Membership.id).where(
                    Membership.user_id == user.id,
                    Membership.organization_id == user.organization_id,
                )
            ):
                session.add(
                    Membership(
                        user_id=user.id,
                        organization_id=user.organization_id,
                        role=ROLES[alias],
                    )
                )
        supplier_id = organization_ids["fornecedor"]
        # Contas conhecidas do seed operam apenas para a distribuidora sintética.
        for user_id, role in ((PLANNER_ID, Role.PLANNER), (USERS[2][0], Role.DRIVER)):
            if not session.scalar(
                select(Membership.id).where(
                    Membership.user_id == user_id,
                    Membership.organization_id == supplier_id,
                    Membership.role == role,
                )
            ):
                session.add(
                    Membership(user_id=user_id, organization_id=supplier_id, role=role)
                )
            user = session.get(User, user_id)
            if session.get(Organization, user.organization_id).kind in {
                "INTERNAL",
                "BUYER",
            }:
                user.organization_id = supplier_id
        warehouse = session.scalar(
            select(Warehouse)
            .where(
                Warehouse.supplier_organization_id == supplier_id,
                Warehouse.demo_run_id == DEMO_RUN_ID,
            )
            .order_by(Warehouse.id)
        )
        if warehouse is None:
            warehouse = Warehouse(
                supplier_organization_id=supplier_id,
                demo_run_id=DEMO_RUN_ID,
                name="Depósito sintético Manaus",
                address="Endereço fictício para demonstração, Manaus",
                opening_hours="Seg–Sex 08:00–18:00",
                region="MANAUS",
            )
            session.add(warehouse)
            session.flush()
        if not session.scalar(
            select(CapacitySlot.id).where(CapacitySlot.warehouse_id == warehouse.id)
        ):
            session.add(
                CapacitySlot(
                    warehouse_id=warehouse.id,
                    starts_at=datetime.now(UTC),
                    ends_at=datetime.now(UTC) + timedelta(days=30),
                    total_quantity=10000,
                )
            )
        if warehouse.geocoding_state == "PENDING":
            warehouse.geocoding_state = "CONFIRMED"
            warehouse.latitude, warehouse.longitude = (
                Decimal("-3.100000"),
                Decimal("-60.000000"),
            )
            warehouse.geocoding_review_reason = (
                "Coordenadas sintéticas, não geocodificação real"
            )
            warehouse.reviewed_by = ADMIN_ID
        specs = [
            (5000, "g", 6, 31000, 60000),
            (1000, "g", 10, 10500, 24000),
            (1000, "g", 10, 10500, 24000),
            (900, "ml", 20, 19000, 40000),
        ]
        for (product_id, name, price), (content, unit, count, weight, volume) in zip(
            PRODUCTS, specs, strict=True
        ):
            product = session.get(Product, product_id)
            if product is None:
                product = Product(id=product_id, name=name, demo_run_id=DEMO_RUN_ID)
                session.add(product)
            if product.brand is None:
                product.brand = "Marca Sintética"
                product.category = "ALIMENTO_SECO_INDUSTRIALIZADO"
                product.net_content, product.net_content_unit = content, unit
                product.packaging_type = (
                    "Fardo lacrado" if unit == "g" else "Caixa lacrada"
                )
                (
                    product.packaging_quantity,
                    product.gross_weight_grams,
                    product.volume_cm3,
                ) = count, weight, volume
                product.variant_attributes = {"origem": "SIMULAÇÃO"}
                product.publication_state = "PUBLISHED"
            session.flush()
            offer = session.scalar(
                select(Offer).where(
                    Offer.product_id == product.id,
                    Offer.demo_run_id == DEMO_RUN_ID,
                    Offer.supplier_organization_id == supplier_id,
                )
            )
            if offer is None:
                offer = Offer(
                    product_id=product.id,
                    supplier_organization_id=supplier_id,
                    warehouse_id=warehouse.id,
                    amount_minor=minor(price),
                    minimum_order_quantity=1,
                    demo_run_id=DEMO_RUN_ID,
                )
                session.add(offer)
                session.flush()
                session.add(
                    OfferPriceVersion(
                        offer_id=offer.id,
                        version=1,
                        amount_minor=offer.amount_minor,
                        minimum_order_quantity=1,
                    )
                )
                session.add(
                    InventoryProjection(
                        offer_id=offer.id,
                        warehouse_id=warehouse.id,
                        external_sellable_quantity=100,
                        demo_run_id=DEMO_RUN_ID,
                    )
                )


def main() -> None:
    load_dotenv(".env")
    settings = get_settings()
    require_demo(settings)
    password = os.getenv("ROTALOG_DEMO_PASSWORD") or getpass(
        "Senha das contas sintéticas: "
    )
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        seed_demo(create_session_factory(engine), settings, password)
    finally:
        engine.dispose()
    print("SIMULAÇÃO: seed V5 concluído; nenhum dado pessoal ou pagamento real.")


if __name__ == "__main__":
    main()
