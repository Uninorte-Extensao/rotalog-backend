"""Extensão opt-in do smoke_demo: rota manual e recebimento sintético comprovado."""

import io
from datetime import UTC, datetime, timedelta

from PIL import Image


def exercise_delivery(
    request,
    login,
    admin_password,
    suffix,
    supplier,
    buyer,
    warehouse,
    fulfillment,
    order,
    offer,
    *,
    account_password,
    mobile=False,
    prepare_only=False,
):
    admin, _ = login("admin@demo.rotalog.invalid", admin_password)
    planner_email = f"planejador-{suffix}@demo.rotalog.invalid"
    request(
        "POST",
        "/api/v1/admin/users",
        expected=201,
        headers=admin,
        json={
            "email": planner_email,
            "name": "Planejador smoke sintético",
            "password": account_password,
            "role": "ROUTE_PLANNER",
            "organization_id": order["supplier_organization_id"],
        },
    )
    planner, _ = login(planner_email, account_password)
    driver_email = f"motorista-{suffix}@demo.rotalog.invalid"
    request(
        "POST",
        "/api/v1/drivers",
        expected=201,
        headers=planner,
        json={
            "email": driver_email,
            "name": "Motorista smoke sintético",
            "password": account_password,
        },
    )
    driver, driver_login = login(driver_email, account_password)
    vehicle = request(
        "POST",
        "/api/v1/vehicles",
        expected=201,
        headers=planner,
        json={
            "registration": f"DEMO-{suffix.upper()}",
            "package_units": 100,
            "weight_grams": 1000000,
            "volume_cm3": 1000000,
        },
    )
    now = datetime.now(UTC)
    proposal = request(
        "POST",
        "/api/v1/route-plan-versions",
        expected=201,
        headers=planner | {"Idempotency-Key": f"plan-{suffix}"},
        json={
            "warehouse_id": warehouse["id"],
            "driver_id": driver_login["user"]["id"],
            "vehicle_id": vehicle["id"],
            "fulfillment_ids": [fulfillment["id"]],
            "reason": "Planejamento manual integral do smoke sintético",
            "deliveries": [
                {
                    "supplier_order_id": order["id"],
                    "address": "Destino fictício smoke Manaus",
                    "latitude": "-3.100000",
                    "longitude": "-60.000000",
                    "starts_at": now.isoformat(),
                    "ends_at": (now + timedelta(hours=4)).isoformat(),
                }
            ],
        },
    )
    approved = request(
        "POST",
        f"/api/v1/route-plan-versions/{proposal['id']}/approve",
        headers=planner | {"Idempotency-Key": f"approve-{suffix}"},
        json={"expected_version": proposal["version"]},
    )
    route = request(
        "POST",
        f"/api/v1/route-plan-versions/{proposal['id']}/publish",
        headers=planner | {"Idempotency-Key": f"publish-{suffix}"},
        json={"expected_version": approved["version"]},
    )
    if prepare_only:
        final = request("GET", f"/api/v1/supplier-orders/{order['id']}", headers=buyer)
        final["_mobile_route_id"] = route["id"]
        inventory = request(
            "GET",
            "/api/v1/inventory",
            headers=supplier,
            params={"offer_id": offer["id"]},
        )["items"][0]
        return final, inventory
    for command in ["accept", "start"] if mobile else ["accept", "pickup", "start"]:
        if mobile and command == "start":
            from smoke_mobile import exercise_cargo

            route = exercise_cargo(request, route, supplier, driver, suffix)
        route = request(
            "POST",
            f"/api/v1/routes/{route['id']}/{command}",
            headers=driver | {"Idempotency-Key": f"{command}-{suffix}"},
            json={"expected_version": route["version"]},
        )
    if mobile:
        from smoke_mobile import exercise_tracking

        exercise_tracking(request, route, driver, suffix)
    stop = next(s for s in route["stops"] if s["kind"] == "DELIVERY")
    attempt = request(
        "POST",
        f"/api/v1/route-stops/{stop['id']}/delivery-attempts",
        expected=201,
        headers=driver | {"Idempotency-Key": f"attempt-{suffix}"},
        json={
            "expected_version": route["version"],
            "device_id": f"synthetic-smoke-{suffix}",
        },
    )
    receiver = request(
        "POST",
        f"/api/v1/delivery-attempts/{attempt['id']}/receiver-authorizations",
        headers=buyer,
        json={"receiver_role": "Recebedor sintético do smoke"},
    )
    stream = io.BytesIO()
    # Arquivos artificiais somente para testar o protocolo: não são evidência real.
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    files = {}
    for kind in ["PHOTO", "SIGNATURE"]:
        if mobile:
            from smoke_mobile import upload_evidence

            files[kind] = upload_evidence(
                request, attempt["id"], kind, stream.getvalue(), driver
            )
        else:
            files[kind] = request(
                "POST",
                f"/api/v1/delivery-attempts/{attempt['id']}/evidence",
                expected=201,
                headers=driver,
                params={"kind": kind},
                files={"file": ("synthetic.png", stream.getvalue(), "image/png")},
            )
    body = {
        "expected_version": attempt["version"],
        "captured_at": datetime.now(UTC).isoformat(),
        "latitude": "-3.100000",
        "longitude": "-60.000000",
        "accuracy_meters": "10.00",
        "receiver_code": receiver["receiver_code"],
        "signature_id": files["SIGNATURE"]["id"],
        "photo_ids": [files["PHOTO"]["id"]],
        "visible_condition": "Lacres sintéticos preservados",
        "package_count": 3,
        "observations": "SIMULAÇÃO: arquivos artificiais de teste",
        "lines": [
            {
                "fulfillment_line_id": fulfillment["lines"][0]["id"],
                "buyer_accepted_quantity": 3,
            }
        ],
    }
    path = f"/api/v1/delivery-attempts/{attempt['id']}/complete"
    headers = driver | {"Idempotency-Key": f"proof-{suffix}"}
    completed = request("POST", path, headers=headers, json=body)
    assert request("POST", path, headers=headers, json=body) == completed
    proof = request(
        "GET", f"/api/v1/delivery-attempts/{attempt['id']}/proof", headers=supplier
    )
    assert proof["validation_state"] == "VALID" and "receiver_code" not in str(proof)
    final = request("GET", f"/api/v1/supplier-orders/{order['id']}", headers=buyer)
    assert (
        final["state"] == "DELIVERED"
        and final["items"][0]["buyer_accepted_quantity"] == 3
    )
    assert final["items"][0]["financially_released_quantity"] == 0
    timeline = request(
        "GET", f"/api/v1/supplier-orders/{order['id']}/timeline", headers=supplier
    )
    assert "DELIVERY_PROOF_VALIDATED" in {e["kind"] for e in timeline["items"]}
    route = request("GET", f"/api/v1/routes/{route['id']}", headers=driver)
    for command in ["end-execution", "complete-operation"]:
        route = request(
            "POST",
            f"/api/v1/routes/{route['id']}/{command}",
            headers=driver | {"Idempotency-Key": f"{command}-{suffix}"},
            json={"expected_version": route["version"]},
        )
    assert route["state"] == "OPERATIONALLY_COMPLETED"
    inventory = request(
        "GET", "/api/v1/inventory", headers=supplier, params={"offer_id": offer["id"]}
    )["items"][0]
    assert (
        inventory["external_sellable_quantity"] == 17
        and inventory["rotalog_reserved_not_reflected_quantity"] == 0
    )
    return final, inventory
