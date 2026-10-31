"""Contratos do frontend: catálogo administrativo, convites e isolamento operacional."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_catalog_ordering import (
    PASSWORD,
    PRODUCT,
    account,
    checkout,
    login,
    ok,
    purchase,
    setup_supply,
)
from test_delivery import _command, prepared

from rotalog.catalog.models import Category, Product
from rotalog.core.database import transactional_session
from rotalog.identity.models import UserInvitation
from rotalog.inventory.models import Warehouse
from rotalog.ordering.models import Fulfillment, FulfillmentLine, OrderItem


def test_categories_and_administrative_catalog(demo_client):
    admin, _ = login(demo_client)
    buyer, _ = login(demo_client, "comprador")
    category = {"code": "GRAOS", "name": "Grãos secos"}
    assert (
        demo_client.post(
            "/api/v1/admin/categories", headers=buyer, json=category
        ).status_code
        == 403
    )
    created = ok(
        demo_client.post("/api/v1/admin/categories", headers=admin, json=category), 201
    )
    assert created["code"] == "GRAOS"
    assert (
        demo_client.post(
            "/api/v1/admin/categories", headers=admin, json=category
        ).status_code
        == 409
    )
    categories = ok(demo_client.get("/api/v1/categories?page_size=1", headers=buyer))
    assert categories["total"] == 2 and len(categories["items"]) == 1
    draft = ok(
        demo_client.post(
            "/api/v1/admin/products",
            headers=admin,
            json=PRODUCT | {"category": "GRAOS"},
        ),
        201,
    )
    assert demo_client.get("/api/v1/admin/products", headers=buyer).status_code == 403
    listed = ok(
        demo_client.get("/api/v1/admin/products?publication_state=DRAFT", headers=admin)
    )
    assert [p["id"] for p in listed["items"]] == [draft["id"]]
    assert draft["id"] not in {
        p["id"] for p in ok(demo_client.get("/api/v1/products", headers=buyer))["items"]
    }
    assert (
        demo_client.post(
            "/api/v1/admin/products",
            headers=admin,
            json=PRODUCT | {"category": "INEXISTENTE"},
        ).status_code
        == 422
    )
    assert (
        demo_client.get("/api/v1/categories?page=0", headers=buyer).status_code == 422
    )


def test_product_edits_and_unpublish_preserve_paid_order(demo_client):
    supplier, _, product, _, offer, _, capacity = setup_supply(demo_client)
    buyer, _ = account(demo_client, "snapshot", "BUYER_ADMIN")
    paid = ok(
        purchase(
            demo_client, buyer, ok(checkout(demo_client, buyer, offer, capacity), 201)
        )
    )
    order_path = f"/api/v1/supplier-orders/{paid['supplier_order_ids'][0]}"
    before = ok(demo_client.get(order_path, headers=buyer))
    admin, _ = login(demo_client)
    path = f"/api/v1/admin/products/{product['id']}"
    current = ok(demo_client.get(f"/api/v1/products/{product['id']}", headers=admin))
    change = {
        "expected_version": current["version"],
        "name": "Novo nome sintético",
        "gross_weight_grams": 600,
    }
    assert demo_client.patch(path, headers=supplier, json=change).status_code == 403
    edited = ok(demo_client.patch(path, headers=admin, json=change))
    assert edited["name"] == change["name"]
    assert demo_client.patch(path, headers=admin, json=change).status_code == 409
    withdrawn = ok(
        demo_client.post(
            path + "/unpublish",
            headers=admin,
            json={"expected_version": edited["version"]},
        )
    )
    assert withdrawn["publication_state"] == "UNPUBLISHED"
    assert (
        demo_client.get(f"/api/v1/products/{product['id']}", headers=buyer).status_code
        == 404
    )
    assert (
        ok(
            demo_client.get(f"/api/v1/offers?product_id={product['id']}", headers=buyer)
        )["total"]
        == 0
    )
    assert (
        demo_client.post(
            "/api/v1/checkouts",
            headers=buyer | {"Idempotency-Key": "withdrawn-checkout"},
            json={
                "items": [{"offer_id": offer["id"], "quantity": 1}],
                "capacity_slot_id": capacity["id"],
                "accepts_partial": False,
            },
        ).status_code
        == 422
    )
    assert ok(demo_client.get(order_path, headers=buyer))["items"] == before["items"]
    listed = ok(
        demo_client.get(
            "/api/v1/admin/products?publication_state=UNPUBLISHED&search=Novo",
            headers=admin,
        )
    )
    assert [p["id"] for p in listed["items"]] == [product["id"]]
    republished = ok(
        demo_client.post(
            path + "/publish",
            headers=admin,
            json={"expected_version": withdrawn["version"]},
        )
    )
    assert republished["publication_state"] == "PUBLISHED"
    assert (
        ok(
            demo_client.get(f"/api/v1/offers?product_id={product['id']}", headers=buyer)
        )["total"]
        == 1
    )


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": None},
        {"net_content": 0},
        {"publication_state": "PUBLISHED"},
        {"version": 20},
    ],
)
def test_product_patch_rejects_invalid_changes(demo_client, changes):
    admin, _ = login(demo_client)
    product = ok(
        demo_client.post("/api/v1/admin/products", headers=admin, json=PRODUCT), 201
    )
    assert (
        demo_client.patch(
            f"/api/v1/admin/products/{product['id']}",
            headers=admin,
            json={"expected_version": product["version"]} | changes,
        ).status_code
        == 422
    )


def test_historical_incomplete_product_can_be_completed(demo_client):
    admin, profile = login(demo_client)
    product_id = uuid4()
    with transactional_session(demo_client.app.state.session_factory) as session:
        session.add(
            Product(
                id=product_id,
                name="Legado incompleto",
                demo_run_id=UUID(profile["demo_run_id"]),
            )
        )
    listed = ok(
        demo_client.get("/api/v1/admin/products?publication_state=DRAFT", headers=admin)
    )
    assert listed["items"][0]["brand"] is None
    changed = ok(
        demo_client.patch(
            f"/api/v1/admin/products/{product_id}",
            headers=admin,
            json={"expected_version": 1} | PRODUCT,
        )
    )
    assert changed["brand"] == PRODUCT["brand"]
    ok(
        demo_client.post(
            f"/api/v1/admin/products/{product_id}/publish",
            headers=admin,
            json={"expected_version": changed["version"]},
        )
    )


def test_planners_cannot_read_or_command_another_supplier(demo_client):
    data = prepared(demo_client)
    other, _ = login(demo_client, "planejador")
    route_id, plan_id = data["route"]["id"], data["plan"]["id"]
    assert ok(demo_client.get("/api/v1/routes", headers=other))["total"] == 0
    assert ok(demo_client.get("/api/v1/vehicles", headers=other)) == []
    assert data["plan_body"]["driver_id"] not in {
        d["id"] for d in ok(demo_client.get("/api/v1/drivers", headers=other))
    }
    for path in [
        f"/routes/{route_id}",
        f"/routes/{route_id}/manifest",
        f"/routes/{route_id}/tracking/current",
        f"/routes/{route_id}/tracking/history",
        f"/route-plan-versions/{plan_id}",
    ]:
        assert demo_client.get("/api/v1" + path, headers=other).status_code == 404
    for action in ["approve", "publish"]:
        assert (
            _command(
                demo_client,
                f"/route-plan-versions/{plan_id}/{action}",
                other,
                1,
                "foreign-plan-command",
            ).status_code
            == 404
        )
    assert (
        _command(
            demo_client, f"/routes/{route_id}/cancel", other, 1, "foreign-route-command"
        ).status_code
        == 404
    )
    assert (
        demo_client.post(
            "/api/v1/route-plan-versions",
            headers=other | {"Idempotency-Key": "foreign-plan-create"},
            json=data["plan_body"],
        ).status_code
        == 404
    )
    assert ok(demo_client.get("/api/v1/routes", headers=data["planner"]))["total"] == 1


@pytest.mark.parametrize("foreign", ["driver_id", "vehicle_id", "fulfillment_ids"])
def test_plan_cannot_mix_foreign_resources_into_own_warehouse(demo_client, foreign):
    data = prepared(demo_client)
    other, _ = login(demo_client, "planejador")
    if foreign == "driver_id":
        value = ok(demo_client.get("/api/v1/drivers", headers=other))[0]["id"]
    elif foreign == "vehicle_id":
        value = ok(
            demo_client.post(
                "/api/v1/vehicles",
                headers=other,
                json={
                    "registration": "OUTRO-FORNECEDOR",
                    "package_units": 20,
                    "weight_grams": 100000,
                    "volume_cm3": 100000,
                },
            ),
            201,
        )["id"]
    else:
        from test_tracking import _ready_order

        _, supplier = login(demo_client, "fornecedor")
        _, buyer = login(demo_client, "comprador")
        order_id, _ = _ready_order(demo_client, supplier, buyer)
        with transactional_session(demo_client.app.state.session_factory) as session:
            warehouse_id = session.scalar(
                select(Warehouse.id).where(
                    Warehouse.supplier_organization_id
                    == UUID(supplier["organization_id"])
                )
            )
            load = Fulfillment(
                supplier_order_id=UUID(order_id),
                warehouse_id=warehouse_id,
                state="READY_TO_DISPATCH",
            )
            session.add(load)
            session.flush()
            value = [str(load.id)]
    response = demo_client.post(
        "/api/v1/route-plan-versions",
        headers=data["planner"] | {"Idempotency-Key": "mixed-resources-plan"},
        json=data["plan_body"] | {foreign: value},
    )
    assert response.status_code == 404


def test_eligible_loads_exclude_assigned_and_foreign_and_reappear_on_cancel(
    demo_client,
):
    data = prepared(demo_client)
    path = "/api/v1/route-planning/eligible-fulfillments"
    assert ok(demo_client.get(path, headers=data["planner"]))["total"] == 0
    ok(
        _command(
            demo_client,
            f"/routes/{data['route']['id']}/cancel",
            data["planner"],
            data["route"]["version"],
            "eligibility-cancel",
        )
    )
    eligible = ok(demo_client.get(path + "?page_size=1", headers=data["planner"]))
    assert eligible["total"] == 1
    item = eligible["items"][0]
    assert item["id"] == data["load"]["id"] and item["package_units"] == 3
    assert item["weight_grams"] == 1350 and item["volume_cm3"] == 3000
    assert (
        ok(demo_client.get(path + "?page=2&page_size=1", headers=data["planner"]))[
            "items"
        ]
        == []
    )
    assert (
        ok(demo_client.get(path + f"?warehouse_id={uuid4()}", headers=data["planner"]))[
            "total"
        ]
        == 0
    )
    other, _ = login(demo_client, "planejador")
    assert ok(demo_client.get(path, headers=other))["total"] == 0
    assert demo_client.get(path, headers=data["supplier"]).status_code == 403
    with transactional_session(demo_client.app.state.session_factory) as session:
        load = session.get(Fulfillment, UUID(data["load"]["id"]))
        load.state = "SEPARATING"
    assert ok(demo_client.get(path, headers=data["planner"]))["total"] == 0


@pytest.mark.parametrize("invalid", ["empty", "dispatched", "dimensions"])
def test_ineligible_lines_are_not_offered_for_planning(demo_client, invalid):
    data = prepared(demo_client)
    ok(
        _command(
            demo_client,
            f"/routes/{data['route']['id']}/cancel",
            data["planner"],
            1,
            "cancel-invalid-load",
        )
    )
    with transactional_session(demo_client.app.state.session_factory) as session:
        line = session.scalar(
            select(FulfillmentLine).where(
                FulfillmentLine.fulfillment_id == UUID(data["load"]["id"])
            )
        )
        if invalid == "empty":
            line.separated_quantity = 0
        elif invalid == "dispatched":
            line.dispatched_quantity = 1
        else:
            item = session.get(OrderItem, line.order_item_id)
            item.product_snapshot = item.product_snapshot | {"gross_weight_grams": None}
    assert (
        ok(
            demo_client.get(
                "/api/v1/route-planning/eligible-fulfillments", headers=data["planner"]
            )
        )["total"]
        == 0
    )


def test_user_provisioning_roles_and_duplicate_email(demo_client):
    admin, admin_profile = login(demo_client)
    _, supplier = login(demo_client, "fornecedor")
    payload = {
        "email": "  PLANEJADOR.NOVO@DEMO.ROTALOG.INVALID  ",
        "name": "Planejador novo",
        "password": PASSWORD,
        "organization_id": supplier["organization_id"],
        "role": "ROUTE_PLANNER",
    }
    user = ok(demo_client.post("/api/v1/admin/users", headers=admin, json=payload), 201)
    assert user["email"] == "planejador.novo@demo.rotalog.invalid"
    assert (
        user["organization_id"] == supplier["organization_id"]
        and "password" not in user
    )
    planner, _ = login(demo_client, "planejador.novo", PASSWORD)
    assert (
        demo_client.post(
            "/api/v1/admin/users", headers=planner, json=payload
        ).status_code
        == 403
    )
    assert (
        demo_client.post("/api/v1/admin/users", headers=admin, json=payload).status_code
        == 409
    )
    invalid = payload | {
        "email": "outro@demo.rotalog.invalid",
        "organization_id": admin_profile["organization_id"],
    }
    assert (
        demo_client.post("/api/v1/admin/users", headers=admin, json=invalid).status_code
        == 422
    )
    internal = invalid | {"role": "ROTALOG_ADMIN"}
    ok(demo_client.post("/api/v1/admin/users", headers=admin, json=internal), 201)
    new_admin, _ = login(demo_client, "outro", PASSWORD)
    ok(demo_client.get("/api/v1/admin/products", headers=new_admin))


def invitation(client):
    admin, _ = login(client)
    _, supplier = login(client, "fornecedor")
    response = client.post(
        "/api/v1/admin/invitations",
        headers=admin,
        json={
            "email": "convite@demo.rotalog.invalid",
            "role": "ROUTE_PLANNER",
            "organization_id": supplier["organization_id"],
        },
    )
    assert response.headers["cache-control"] == "no-store"
    return admin, ok(response, 201)


def test_invitation_acceptance_is_single_use_and_hash_only(demo_client):
    _, invite = invitation(demo_client)
    with transactional_session(demo_client.app.state.session_factory) as session:
        stored = session.get(UserInvitation, UUID(invite["id"]))
        assert (
            stored.token_hash != invite["invitation_token"]
            and len(stored.token_hash) == 64
        )
    body = {
        "token": invite["invitation_token"],
        "name": "Convidado Sintético",
        "password": PASSWORD,
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: demo_client.post(
                    "/api/v1/auth/invitations/accept", json=body
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 410]
    planner, profile = login(demo_client, "convite", PASSWORD)
    assert profile["organization_id"] == invite["organization_id"]
    assert profile["role"] == "ROUTE_PLANNER"
    assert ok(demo_client.get("/api/v1/routes", headers=planner))["total"] == 0


@pytest.mark.parametrize("invalid", ["expired", "revoked", "reissued"])
def test_invitations_expire_and_can_be_revoked_or_replaced(demo_client, invalid):
    admin, invite = invitation(demo_client)
    if invalid == "expired":
        with transactional_session(demo_client.app.state.session_factory) as session:
            session.get(UserInvitation, UUID(invite["id"])).expires_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
    elif invalid == "revoked":
        assert (
            demo_client.delete(
                f"/api/v1/admin/invitations/{invite['id']}", headers=admin
            ).status_code
            == 204
        )
    else:
        ok(
            demo_client.post(
                "/api/v1/admin/invitations",
                headers=admin,
                json={
                    "email": invite["email"],
                    "role": invite["role"],
                    "organization_id": invite["organization_id"],
                },
            ),
            201,
        )
    response = demo_client.post(
        "/api/v1/auth/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "name": "Convite inválido",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 410


def test_categories_are_isolated_by_demo_run(demo_client):
    from rotalog.demo.models import DemoRun

    admin, _ = login(demo_client)
    with transactional_session(demo_client.app.state.session_factory) as session:
        run = DemoRun(name="Outra execução")
        session.add(run)
        session.flush()
        session.add(
            Category(code="PRIVADA", name="Outra categoria", demo_run_id=run.id)
        )
    assert "PRIVADA" not in {
        c["code"]
        for c in ok(demo_client.get("/api/v1/categories", headers=admin))["items"]
    }
