"""Migrações reais em PostgreSQL: vazio, snapshots e preservação de valores."""

import hashlib
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError

from rotalog.catalog.models import Offer, Product
from rotalog.core.database import transactional_session
from rotalog.inventory.models import InventoryProjection


def test_frontend_upgrade_recovers_owners_without_guessing(
    demo_client, alembic_config, database_url
):
    from test_delivery import prepared

    data = prepared(demo_client)
    command.downgrade(alembic_config, "20261023_0009")
    engine = sa.create_engine(database_url)
    unassigned_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO vehicle(id,demo_run_id,registration,package_units,weight_grams,volume_cm3) "
                    "SELECT :id,demo_run_id,'SEM-VINCULO',10,100,100 FROM vehicle LIMIT 1"
                ),
                {"id": unassigned_id},
            )
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            supplier_id = connection.scalar(
                sa.text(
                    "SELECT supplier_organization_id FROM supplier_order WHERE id=:id"
                ),
                {"id": data["order"]["id"]},
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT supplier_organization_id FROM route_plan_version WHERE id=:id"
                    ),
                    {"id": data["plan"]["id"]},
                )
                == supplier_id
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT supplier_organization_id FROM vehicle WHERE id=:id"
                    ),
                    {"id": data["route"]["vehicle_id"]},
                )
                == supplier_id
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT supplier_organization_id FROM vehicle WHERE id=:id"
                    ),
                    {"id": unassigned_id},
                )
                is None
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM route")) == 1
            assert connection.scalar(sa.text("SELECT count(*) FROM category")) == 1
    finally:
        engine.dispose()


def test_mobile_upgrade_preserves_previous_route_private_blob_and_product_file(
    demo_client, alembic_config, database_url
):
    from uuid import UUID

    from test_catalog_ordering import login, ok
    from test_delivery import _image, prepared, started

    from rotalog.logistics.delivery_models import Route

    data = started(demo_client, prepared(demo_client))
    admin, profile = login(demo_client)
    route_id = data["route"]["id"]
    attempt_id = data["attempt"]["id"]
    product_id = data["order"]["items"][0]["product"]["id"]
    evidence_id, file_id = uuid4(), uuid4()
    content = _image()
    digest = hashlib.sha256(content).hexdigest()
    command.downgrade(alembic_config, "20261023_0008")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO private_evidence(id,attempt_id,uploaded_by,kind,mime_type,sha256,content) SELECT :id,:attempt,a.driver_id,'PHOTO','image/png',:digest,:content FROM delivery_attempt a WHERE a.id=:attempt"
                ),
                {
                    "id": evidence_id,
                    "attempt": UUID(attempt_id),
                    "digest": digest,
                    "content": content,
                },
            )
            c.execute(
                sa.text(
                    "INSERT INTO stored_file(id,product_id,demo_run_id,organization_id,uploaded_by,purpose,status,size_bytes,mime_type,sha256,expires_at) SELECT :id,p.id,p.demo_run_id,u.organization_id,u.id,'PRODUCT_IMAGE','PENDING_UPLOAD',:size,'image/png',:digest,now()+interval '15 minutes' FROM product p JOIN identity_user u ON u.id=:admin WHERE p.id=:product"
                ),
                {
                    "id": file_id,
                    "size": len(content),
                    "digest": digest,
                    "admin": UUID(profile["id"]),
                    "product": UUID(product_id),
                },
            )
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        assert (
            demo_client.get(
                f"/api/v1/delivery-evidence/{evidence_id}", headers=data["buyer"]
            ).content
            == content
        )
        assert (
            ok(demo_client.get(f"/api/v1/files/{file_id}", headers=admin))["product_id"]
            == product_id
        )
        route = ok(
            demo_client.get(f"/api/v1/routes/{route_id}", headers=data["driver"])
        )
        assert route["state"] == "STARTED" and route["started_at"]
        with transactional_session(demo_client.app.state.session_factory) as session:
            assert session.get(Route, UUID(route_id)).started_at is not None
        assert (
            ok(
                demo_client.get(
                    f"/api/v1/routes/{route_id}/manifest", headers=data["driver"]
                )
            )["inspection_required"]
            is False
        )
    finally:
        engine.dispose()


def test_migrations_run_on_empty_database(alembic_config, database_url):
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        assert {
            "product",
            "offer",
            "inventory_projection",
            "warehouse",
            "membership",
            "auth_session",
            "refresh_token",
            "checkout_session",
            "purchase",
            "supplier_order",
            "order_item",
            "payment",
            "payment_allocation",
            "stored_file",
            "file_event",
            "file_download_grant",
            "product_image",
            "product_image_event",
            "cargo_inspection",
            "cargo_issue",
            "cargo_event",
            "location_batch",
            "route_location_point",
            "current_route_position",
        } <= set(inspector.get_table_names())
        columns = {column["name"]: column for column in inspector.get_columns("offer")}
        assert isinstance(columns["amount_minor"]["type"], sa.BigInteger)
        assert "unit_price" not in columns
        assert {column["name"]: column for column in inspector.get_columns("product")}[
            "created_at"
        ]["type"].timezone
    finally:
        engine.dispose()
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_product_images_upgrade_preserves_previous_catalog(
    alembic_config, database_url
):
    command.upgrade(alembic_config, "20261023_0007")
    run_id, product_id = uuid4(), uuid4()
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO demo_run(id,name) VALUES (:id,'Snapshot sintético')"
                ),
                {"id": run_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO product(id,name,demo_run_id,publication_state) VALUES (:id,'Catálogo preservado',:run,'PUBLISHED')"
                ),
                {"id": product_id, "run": run_id},
            )
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT name,publication_state FROM product WHERE id=:id"),
                {"id": product_id},
            ).one() == ("Catálogo preservado", "PUBLISHED")
            assert connection.scalar(sa.text("SELECT count(*) FROM stored_file")) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM product_image")) == 0
        command.downgrade(alembic_config, "20261023_0007")
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "snapshot", ["20261013_0001", "20261013_0002", "20261018_0003"]
)
def test_v5_preserves_previous_snapshots(alembic_config, database_url, snapshot):
    command.upgrade(alembic_config, snapshot)
    product_id, offer_id = uuid4(), uuid4()
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO product (id, name) VALUES (:id, 'Produto anterior')"
                ),
                {"id": product_id},
            )
            if snapshot != "20261013_0001":
                connection.execute(
                    sa.text(
                        "INSERT INTO offer (id, product_id, supplier_organization_id, unit_price, minimum_order_quantity) VALUES (:id, :product, :supplier, 42.50, 1)"
                    ),
                    {"id": offer_id, "product": product_id, "supplier": uuid4()},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO inventory_balance (offer_id, available_quantity, reserved_quantity) VALUES (:id, 10, 2)"
                    ),
                    {"id": offer_id},
                )
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT name, publication_state FROM product WHERE id=:id"),
                {"id": product_id},
            ).one() == ("Produto anterior", "DRAFT")
            if snapshot != "20261013_0001":
                assert connection.execute(
                    sa.text("SELECT amount_minor, currency FROM offer WHERE id=:id"),
                    {"id": offer_id},
                ).one() == (4250, "BRL")
                assert connection.execute(
                    sa.text(
                        "SELECT external_sellable_quantity, rotalog_reserved_not_reflected_quantity FROM inventory_projection WHERE offer_id=:id"
                    ),
                    {"id": offer_id},
                ).one() == (10, 2)
        command.downgrade(alembic_config, snapshot)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT name FROM product WHERE id=:id"), {"id": product_id}
                )
                == "Produto anterior"
            )
            if snapshot != "20261013_0001":
                assert connection.scalar(
                    sa.text("SELECT unit_price FROM offer WHERE id=:id"),
                    {"id": offer_id},
                ) == Decimal("42.50")
                assert connection.execute(
                    sa.text(
                        "SELECT available_quantity, reserved_quantity FROM inventory_balance WHERE offer_id=:id"
                    ),
                    {"id": offer_id},
                ).one() == (10, 2)
    finally:
        engine.dispose()


def test_database_constraints_reject_invalid_values(demo_client):
    factory = demo_client.app.state.session_factory
    with transactional_session(factory) as session:
        product_id = session.scalar(sa.select(Product.id))
    for amount, minimum in [(-1, 1), (1000, 0)]:
        with pytest.raises(IntegrityError), transactional_session(factory) as session:
            session.add(
                Offer(
                    product_id=product_id,
                    supplier_organization_id=uuid4(),
                    amount_minor=amount,
                    minimum_order_quantity=minimum,
                )
            )
    for total, reserved, safety in [(-1, 0, 0), (1, -1, 0), (1, 2, 0), (1, 1, 1)]:
        with pytest.raises(IntegrityError), transactional_session(factory) as session:
            offer = Offer(
                product_id=product_id,
                supplier_organization_id=uuid4(),
                amount_minor=1000,
                minimum_order_quantity=1,
            )
            session.add(offer)
            session.flush()
            session.add(
                InventoryProjection(
                    offer_id=offer.id,
                    external_sellable_quantity=total,
                    rotalog_reserved_not_reflected_quantity=reserved,
                    safety_stock_quantity=safety,
                )
            )


def test_downgrade_after_demo_use_is_blocked(demo_client, alembic_config):
    with pytest.raises(RuntimeError, match="Downgrade V5 bloqueado"):
        command.downgrade(alembic_config, "20261018_0003")


def test_delivery_upgrade_preserves_legacy_routes_and_reservations(
    alembic_config, database_url
):
    """Uma rota GPS concluída não é migrada para entrega comprovada."""
    command.upgrade(alembic_config, "20261022_0006")
    names = (
        "run",
        "supplier",
        "buyer",
        "driver",
        "warehouse",
        "capacity",
        "checkout",
        "purchase",
        "order",
        "route",
        "point",
        "product",
        "offer",
        "projection",
        "reservation",
    )
    ids = {name: uuid4() for name in names}
    statements = [
        "INSERT INTO demo_run(id,name) VALUES (:run,'snapshot tracking sintético')",
        "INSERT INTO organization(id,name,demo_run_id) VALUES (:supplier,'Fornecedor anterior',:run),(:buyer,'Comprador anterior',:run)",
        "INSERT INTO identity_user(id,email,name,password_hash,role,organization_id,demo_run_id) VALUES (:driver,'snapshot@demo.rotalog.invalid','Motorista anterior','hash preservado','DRIVER',:supplier,:run)",
        "INSERT INTO warehouse(id,supplier_organization_id,demo_run_id,name,address,opening_hours,region) VALUES (:warehouse,:supplier,:run,'Origem','Rua fictícia','Seg–Sex 08–18','MANAUS')",
        "INSERT INTO capacity_slot(id,warehouse_id,starts_at,ends_at,total_quantity,reserved_quantity) VALUES (:capacity,:warehouse,now(),now()+interval '1 day',100,3)",
        "INSERT INTO checkout_session(id,buyer_organization_id,supplier_organization_id,warehouse_id,capacity_slot_id,demo_run_id,expires_at) VALUES (:checkout,:buyer,:supplier,:warehouse,:capacity,:run,now()+interval '1 hour')",
        "INSERT INTO purchase(id,checkout_id,buyer_organization_id,demo_run_id) VALUES (:purchase,:checkout,:buyer,:run)",
        "INSERT INTO supplier_order(id,purchase_id,buyer_organization_id,supplier_organization_id,warehouse_id,demo_run_id,state,acceptance_deadline) VALUES (:order,:purchase,:buyer,:supplier,:warehouse,:run,'READY_TO_DISPATCH',now()+interval '1 day')",
        "INSERT INTO delivery_route(id,supplier_order_id,driver_id,demo_run_id,state,last_latitude,last_longitude,last_position) VALUES (:route,:order,:driver,:run,'COMPLETED',-3.1,-60,public.ST_SetSRID(public.ST_MakePoint(-60,-3.1),4326)::public.geography)",
        "INSERT INTO driver_location_point(id,route_id,driver_id,demo_run_id,latitude,longitude,accuracy_meters,recorded_at,position) VALUES (:point,:route,:driver,:run,-3.1,-60,10,now(),public.ST_SetSRID(public.ST_MakePoint(-60,-3.1),4326)::public.geography)",
        "INSERT INTO product(id,name,demo_run_id) VALUES (:product,'Arroz anterior',:run)",
        "INSERT INTO offer(id,product_id,supplier_organization_id,warehouse_id,demo_run_id,amount_minor,minimum_order_quantity) VALUES (:offer,:product,:supplier,:warehouse,:run,2990,1)",
        "INSERT INTO inventory_projection(id,offer_id,warehouse_id,demo_run_id,external_sellable_quantity,rotalog_reserved_not_reflected_quantity) VALUES (:projection,:offer,:warehouse,:run,20,3)",
        "INSERT INTO reservation(id,checkout_id,projection_id,capacity_slot_id,quantity,expires_at) VALUES (:reservation,:checkout,:projection,:capacity,3,now()+interval '1 hour')",
    ]
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            for sql in statements:
                connection.execute(sa.text(sql), ids)
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    sa.text("SELECT state FROM delivery_route WHERE id=:route"), ids
                )
                == "COMPLETED"
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM driver_location_point WHERE id=:point"
                    ),
                    ids,
                )
                == 1
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT consumed_quantity FROM reservation WHERE id=:reservation"
                    ),
                    ids,
                )
                == 0
            )
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT rotalog_reserved_not_reflected_quantity FROM inventory_projection WHERE id=:projection"
                    ),
                    ids,
                )
                == 3
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM route")) == 0
            assert (
                connection.scalar(sa.text("SELECT count(*) FROM delivery_proof")) == 0
            )
    finally:
        engine.dispose()


def test_v41_identity_membership_and_stock_are_preserved(alembic_config, database_url):
    command.upgrade(alembic_config, "20261018_0003")
    identifiers = {name: uuid4() for name in ("run", "org", "user", "product", "offer")}
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            for sql in [
                "INSERT INTO demo_run (id,name) VALUES (:run, 'execução anterior sintética')",
                "INSERT INTO organization (id,name,demo_run_id) VALUES (:org,'Fornecedor anterior',:run)",
                "INSERT INTO identity_user (id,email,name,password_hash,role,organization_id,demo_run_id) VALUES (:user,'anterior@demo.rotalog.invalid','Anterior','hash-anterior-preservado','fornecedor',:org,:run)",
                "INSERT INTO product (id,name,demo_run_id) VALUES (:product,'Variante anterior',:run)",
                "INSERT INTO offer (id,product_id,supplier_organization_id,unit_price,minimum_order_quantity,demo_run_id) VALUES (:offer,:product,:org,29.90,1,:run)",
                "INSERT INTO inventory_balance (offer_id,available_quantity,reserved_quantity,demo_run_id) VALUES (:offer,20,3,:run)",
            ]:
                connection.execute(sa.text(sql), identifiers)
        command.upgrade(alembic_config, "head")
        command.check(alembic_config)
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT role,password_hash FROM identity_user WHERE id=:user"),
                identifiers,
            ).one() == ("SUPPLIER_OPERATOR", "hash-anterior-preservado")
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT role FROM membership WHERE user_id=:user AND organization_id=:org"
                    ),
                    identifiers,
                )
                == "SUPPLIER_OPERATOR"
            )
            assert (
                connection.scalar(
                    sa.text("SELECT kind FROM organization WHERE id=:org"), identifiers
                )
                == "SUPPLIER"
            )
            row = connection.execute(
                sa.text(
                    "SELECT o.amount_minor,o.warehouse_id,p.warehouse_id,p.external_sellable_quantity,p.rotalog_reserved_not_reflected_quantity FROM offer o JOIN inventory_projection p ON p.offer_id=o.id WHERE o.id=:offer"
                ),
                identifiers,
            ).one()
            assert row[0] == 2990 and row[1] == row[2] and row[1] is not None
            assert tuple(row[3:]) == (20, 3)
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT freshness_state FROM inventory_projection WHERE offer_id=:offer"
                    ),
                    identifiers,
                )
                == "RECONCILING"
            )
    finally:
        engine.dispose()
