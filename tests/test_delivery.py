"""HTTP real: cadastro → checkout → coleta → prova → acompanhamento."""

import io
from datetime import UTC, datetime, timedelta

from PIL import Image
from sqlalchemy import func, select
from test_catalog_ordering import (
    PASSWORD,
    account,
    checkout,
    login,
    ok,
    purchase,
    setup_supply,
)

from rotalog.core.database import transactional_session
from rotalog.logistics.delivery_models import CustodyEvent, DeliveryOutcomeLine


def _command(client, path, headers, version, key):
    return client.post(
        f"/api/v1{path}",
        headers=headers | {"Idempotency-Key": key},
        json={"expected_version": version},
    )


def prepared(client):
    supplier, profile, _product, warehouse, offer, _balance, capacity = setup_supply(
        client
    )
    buyer, _buyer_profile = account(client, "recebedor", "BUYER_ADMIN")
    paid = ok(
        purchase(client, buyer, ok(checkout(client, buyer, offer, capacity), 201))
    )
    order = ok(
        client.get(
            f"/api/v1/supplier-orders/{paid['supplier_order_ids'][0]}", headers=supplier
        )
    )
    accepted = ok(
        client.post(
            f"/api/v1/supplier-orders/{order['id']}/acceptance",
            headers=supplier | {"Idempotency-Key": "delivery-accept-01"},
            json={
                "expected_version": order["version"],
                "lines": [
                    {"order_item_id": order["items"][0]["id"], "accepted_quantity": 3}
                ],
            },
        )
    )
    sep = ok(
        _command(
            client,
            f"/supplier-orders/{order['id']}/start-separation",
            supplier,
            accepted["version"],
            "unused-header",
        )
    )
    load = ok(
        client.post(
            f"/api/v1/supplier-orders/{order['id']}/fulfillments",
            headers=supplier,
            json={
                "expected_version": sep["version"],
                "lines": [
                    {"order_item_id": order["items"][0]["id"], "allocated_quantity": 3}
                ],
            },
        ),
        201,
    )
    separated = ok(
        client.post(
            f"/api/v1/fulfillments/{load['id']}/separation-results",
            headers=supplier,
            json={
                "expected_version": load["version"],
                "lines": [
                    {
                        "fulfillment_line_id": load["lines"][0]["id"],
                        "separated_quantity": 3,
                    }
                ],
            },
        )
    )
    ok(
        _command(
            client,
            f"/fulfillments/{load['id']}/mark-ready",
            supplier,
            separated["version"],
            "unused-header",
        )
    )
    admin, _ = login(client)
    ok(
        client.post(
            "/api/v1/admin/users",
            headers=admin,
            json={
                "email": "planejador.pizza@demo.rotalog.invalid",
                "name": "Planejador Pizza Sintético",
                "password": PASSWORD,
                "role": "ROUTE_PLANNER",
                "organization_id": profile["organization_id"],
            },
        ),
        201,
    )
    planner, _ = login(client, "planejador.pizza", PASSWORD)
    driver_user = ok(
        client.post(
            "/api/v1/drivers",
            headers=planner,
            json={
                "email": "motorista.pizza@demo.rotalog.invalid",
                "name": "Motorista Pizza Sintético",
                "password": PASSWORD,
            },
        ),
        201,
    )
    driver, _ = login(client, "motorista.pizza", PASSWORD)
    vehicle = ok(
        client.post(
            "/api/v1/vehicles",
            headers=planner,
            json={
                "registration": "SINTETICO-001",
                "package_units": 20,
                "weight_grams": 100000,
                "volume_cm3": 100000,
            },
        ),
        201,
    )
    now = datetime.now(UTC)
    body = {
        "warehouse_id": warehouse["id"],
        "driver_id": driver_user["id"],
        "vehicle_id": vehicle["id"],
        "fulfillment_ids": [load["id"]],
        "reason": "Rota manual sintética validada",
        "deliveries": [
            {
                "supplier_order_id": order["id"],
                "address": "Destino sintético Manaus",
                "latitude": "-3.100000",
                "longitude": "-60.000000",
                "starts_at": now.isoformat(),
                "ends_at": (now + timedelta(hours=4)).isoformat(),
            }
        ],
    }
    plan = ok(
        client.post(
            "/api/v1/route-plan-versions",
            headers=planner | {"Idempotency-Key": "manual-plan-test-01"},
            json=body,
        ),
        201,
    )
    approved = ok(
        _command(
            client,
            f"/route-plan-versions/{plan['id']}/approve",
            planner,
            plan["version"],
            "plan-approve-test-01",
        )
    )
    route = ok(
        _command(
            client,
            f"/route-plan-versions/{plan['id']}/publish",
            planner,
            approved["version"],
            "plan-publish-test-01",
        )
    )
    return {
        "supplier": supplier,
        "buyer": buyer,
        "planner": planner,
        "driver": driver,
        "order": order,
        "load": load,
        "offer": offer,
        "route": route,
        "plan": plan,
        "plan_body": body,
    }


def started(client, data):
    route = data["route"]
    for command in ["accept", "pickup", "start"]:
        route = ok(
            _command(
                client,
                f"/routes/{route['id']}/{command}",
                data["driver"],
                route["version"],
                f"route-{command}-test-01",
            )
        )
    data["route"] = route
    stop = next(s for s in route["stops"] if s["kind"] == "DELIVERY")
    attempt = ok(
        client.post(
            f"/api/v1/route-stops/{stop['id']}/delivery-attempts",
            headers=data["driver"] | {"Idempotency-Key": "attempt-create-test-01"},
            json={
                "expected_version": route["version"],
                "device_id": "synthetic-device-01",
            },
        ),
        201,
    )
    data["attempt"] = attempt
    return data


def _image():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    return stream.getvalue()


def proof_body(client, data):
    attempt = data["attempt"]
    ids = {}
    for kind in ["PHOTO", "SIGNATURE"]:
        item = ok(
            client.post(
                f"/api/v1/delivery-attempts/{attempt['id']}/evidence",
                headers=data["driver"],
                params={"kind": kind},
                files={"file": ("synthetic.png", _image(), "image/png")},
            ),
            201,
        )
        ids[kind] = item["id"]
    authorization = ok(
        client.post(
            f"/api/v1/delivery-attempts/{attempt['id']}/receiver-authorizations",
            headers=data["buyer"],
            json={"receiver_role": "Responsável sintético pelo recebimento"},
        )
    )
    return {
        "expected_version": attempt["version"],
        "captured_at": datetime.now(UTC).isoformat(),
        "latitude": "-3.100000",
        "longitude": "-60.000000",
        "accuracy_meters": "10.00",
        "receiver_code": authorization["receiver_code"],
        "signature_id": ids["SIGNATURE"],
        "photo_ids": [ids["PHOTO"]],
        "visible_condition": "Embalagens lacradas sem avarias",
        "package_count": 3,
        "lines": [
            {
                "fulfillment_line_id": data["load"]["lines"][0]["id"],
                "buyer_accepted_quantity": 3,
            }
        ],
    }


def test_delivery_end_to_end_and_private_proof(demo_client):
    data = started(demo_client, prepared(demo_client))
    supplier, buyer, driver, route, attempt = (
        data[k] for k in ["supplier", "buyer", "driver", "route", "attempt"]
    )
    order_path = f"/api/v1/supplier-orders/{data['order']['id']}"
    dispatched = ok(demo_client.get(order_path, headers=supplier))
    assert dispatched["state"] == "DISPATCHED"
    assert dispatched["items"][0]["dispatched_quantity"] == 3
    assert dispatched["items"][0]["reserved_quantity"] == 0
    assert dispatched["delivery_routes"][0]["state"] == "STARTED"
    balance = ok(demo_client.get("/api/v1/inventory", headers=supplier))["items"][0]
    assert balance["external_sellable_quantity"] == 17
    assert balance["rotalog_reserved_not_reflected_quantity"] == 0
    assert balance["effective_sellable_quantity"] == 17
    body = proof_body(demo_client, data)
    path = f"/api/v1/delivery-attempts/{attempt['id']}/complete"
    proof_headers = driver | {"Idempotency-Key": "delivery-complete-test-01"}
    completed = ok(demo_client.post(path, headers=proof_headers, json=body))
    assert completed["validation_state"] == "VALID"
    assert completed["contestation_deadline"] is not None
    assert ok(demo_client.post(path, headers=proof_headers, json=body)) == completed
    delivered = ok(demo_client.get(order_path, headers=buyer))
    assert delivered["state"] == "DELIVERED"
    assert delivered["items"][0]["delivered_quantity"] == 3
    assert delivered["items"][0]["buyer_accepted_quantity"] == 3
    assert delivered["items"][0]["financially_released_quantity"] == 0
    proof = ok(
        demo_client.get(
            f"/api/v1/delivery-attempts/{attempt['id']}/proof", headers=supplier
        )
    )
    assert proof["validation_state"] == "VALID"
    assert "receiver_code" not in str(proof)
    assert proof["evidence"]["distance_meters"] == 0
    file_path = proof["evidence"]["evidence"][0]["download_path"]
    assert demo_client.get(file_path).status_code == 401
    downloaded = demo_client.get(file_path, headers=buyer)
    assert downloaded.status_code == 200 and downloaded.content == _image()
    assert downloaded.headers["cache-control"] == "no-store"
    stranger, _ = account(demo_client, "estranho", "BUYER_ADMIN")
    for private_path in [
        order_path,
        f"/api/v1/routes/{route['id']}",
        file_path,
        f"/api/v1/delivery-attempts/{attempt['id']}/proof",
    ]:
        assert demo_client.get(private_path, headers=stranger).status_code == 404
    latest = ok(demo_client.get(f"/api/v1/routes/{route['id']}", headers=driver))
    ended = ok(
        _command(
            demo_client,
            f"/routes/{route['id']}/end-execution",
            driver,
            latest["version"],
            "route-end-test-01",
        )
    )
    reconciled = ok(
        _command(
            demo_client,
            f"/routes/{route['id']}/complete-operation",
            driver,
            ended["version"],
            "route-close-test-01",
        )
    )
    assert reconciled["state"] == "OPERATIONALLY_COMPLETED"
    events = ok(demo_client.get(order_path + "/timeline", headers=supplier))["items"]
    assert "DELIVERY_PROOF_VALIDATED" in {event["kind"] for event in events}
    with transactional_session(demo_client.app.state.session_factory) as session:
        assert (
            session.scalar(select(func.count()).select_from(DeliveryOutcomeLine)) == 1
        )
        assert session.scalar(select(func.count()).select_from(CustodyEvent)) == 2


def test_pending_proof_does_not_deliver_or_close_operation(demo_client):
    data = started(demo_client, prepared(demo_client))
    body = proof_body(demo_client, data)
    code = body.pop("receiver_code")
    attempt = data["attempt"]
    endpoint = f"/api/v1/delivery-attempts/{attempt['id']}/complete"
    pending = ok(
        demo_client.post(
            endpoint,
            headers=data["driver"] | {"Idempotency-Key": "pending-proof-test-01"},
            json=body,
        )
    )
    assert (
        pending["validation_state"] == "PROOF_PENDING"
        and pending["contestation_deadline"] is None
    )
    order = ok(
        demo_client.get(
            f"/api/v1/supplier-orders/{data['order']['id']}", headers=data["supplier"]
        )
    )
    assert (
        order["state"] == "DISPATCHED" and order["items"][0]["delivered_quantity"] == 0
    )
    latest = ok(
        demo_client.get(f"/api/v1/routes/{data['route']['id']}", headers=data["driver"])
    )
    ended = ok(
        _command(
            demo_client,
            f"/routes/{latest['id']}/end-execution",
            data["driver"],
            latest["version"],
            "end-pending-route-01",
        )
    )
    assert (
        _command(
            demo_client,
            f"/routes/{latest['id']}/complete-operation",
            data["driver"],
            ended["version"],
            "close-pending-route-01",
        ).status_code
        == 409
    )
    body.update(receiver_code=code, expected_version=pending["version"])
    assert (
        ok(
            demo_client.post(
                endpoint,
                headers=data["driver"] | {"Idempotency-Key": "valid-after-pending-01"},
                json=body,
            )
        )["state"]
        == "COMPLETED"
    )


def test_receiver_code_attempt_limit_persists_and_is_not_public(demo_client):
    data = started(demo_client, prepared(demo_client))
    body = proof_body(demo_client, data)
    code = body["receiver_code"]
    body["receiver_code"] = "000000" if code != "000000" else "111111"
    endpoint = f"/api/v1/delivery-attempts/{data['attempt']['id']}/complete"
    for i in range(5):
        assert (
            demo_client.post(
                endpoint,
                headers=data["driver"] | {"Idempotency-Key": f"bad-receiver-code-{i}"},
                json=body,
            ).status_code
            == 403
        )
    body["receiver_code"] = code
    assert (
        demo_client.post(
            endpoint,
            headers=data["driver"] | {"Idempotency-Key": "locked-receiver-code-01"},
            json=body,
        ).status_code
        == 403
    )
    assert (
        demo_client.post(
            f"/api/v1/delivery-attempts/{data['attempt']['id']}/receiver-authorizations",
            headers=data["driver"],
            json={"receiver_role": "Não autorizado"},
        ).status_code
        == 403
    )


def test_planner_only_capacity_duplicate_load_and_stale_version(demo_client):
    data = prepared(demo_client)
    body = data["plan_body"]
    assert (
        demo_client.post(
            "/api/v1/route-plan-versions",
            headers=data["supplier"] | {"Idempotency-Key": "supplier-cannot-plan"},
            json=body,
        ).status_code
        == 403
    )
    assert (
        demo_client.post(
            "/api/v1/route-plan-versions",
            headers=data["planner"] | {"Idempotency-Key": "duplicate-load-plan-01"},
            json=body,
        ).status_code
        == 409
    )
    route = data["route"]
    assert (
        _command(
            demo_client,
            f"/routes/{route['id']}/pickup",
            data["driver"],
            route["version"],
            "pickup-too-early-01",
        ).status_code
        == 409
    )
    accepted = ok(
        _command(
            demo_client,
            f"/routes/{route['id']}/accept",
            data["driver"],
            route["version"],
            "accept-once-test-01",
        )
    )
    assert (
        _command(
            demo_client,
            f"/routes/{route['id']}/pickup",
            data["driver"],
            route["version"],
            "pickup-stale-test-01",
        ).status_code
        == 409
    )
    picked = ok(
        _command(
            demo_client,
            f"/routes/{route['id']}/pickup",
            data["driver"],
            accepted["version"],
            "pickup-once-test-01",
        )
    )
    assert (
        ok(
            _command(
                demo_client,
                f"/routes/{route['id']}/pickup",
                data["driver"],
                accepted["version"],
                "pickup-once-test-01",
            )
        )
        == picked
    )


def test_planner_provisions_driver_and_other_roles_are_denied(demo_client):
    planner, _ = login(demo_client, "planejador")
    supplier, _ = account(demo_client, "cadastro-fornecedor", "SUPPLIER_OPERATOR")
    payload = {
        "email": "novo.motorista@demo.rotalog.invalid",
        "password": "senha-inicial-sintetica-2026",
        "name": "Motorista de Teste",
    }

    denied = demo_client.post("/api/v1/drivers", headers=supplier, json=payload)
    assert denied.status_code == 403
    public_registration = demo_client.post(
        "/api/v1/auth/register",
        json=payload | {"organization_name": "Motoristas Sintéticos", "role": "DRIVER"},
    )
    assert public_registration.status_code == 422

    created = ok(
        demo_client.post("/api/v1/drivers", headers=planner, json=payload), 201
    )
    assert created["role"] == "DRIVER"
    assert created["name"] == payload["name"]
    assert created["organizations"][0]["role"] == "DRIVER"
    assert any(
        item["id"] == created["id"]
        for item in ok(demo_client.get("/api/v1/drivers", headers=planner))
    )

    authenticated, user = login(
        demo_client, "novo.motorista", "senha-inicial-sintetica-2026"
    )
    assert user["id"] == created["id"]
    assert (
        demo_client.get("/api/v1/me", headers=authenticated).json()["role"] == "DRIVER"
    )


def test_evidence_rejects_non_images_and_other_attempt(demo_client):
    data = started(demo_client, prepared(demo_client))
    endpoint = f"/api/v1/delivery-attempts/{data['attempt']['id']}/evidence"
    response = demo_client.post(
        endpoint,
        headers=data["driver"],
        params={"kind": "PHOTO"},
        files={"file": ("pretends.png", b"<script>invalid</script>", "image/png")},
    )
    assert response.status_code == 422
    assert (
        demo_client.post(
            endpoint,
            headers=data["supplier"],
            params={"kind": "PHOTO"},
            files={"file": ("valid.png", _image(), "image/png")},
        ).status_code
        == 403
    )
