"""Teste HTTP opt-in: cria somente dados sintéticos e não imprime credenciais."""

import argparse
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    delivery_options = parser.add_mutually_exclusive_group()
    delivery_options.add_argument(
        "--with-delivery",
        action="store_true",
        help="Inclui rota manual, coleta e entrega integral com prova sintética",
    )
    delivery_options.add_argument(
        "--with-mobile",
        action="store_true",
        help="Inclui conferência, divergência, recebimento, GPS canônico e prova via worker",
    )
    delivery_options.add_argument(
        "--prepare-mobile",
        action="store_true",
        help="Cria cenário sintético e deixa a rota publicada para o motorista testar no app",
    )
    parser.add_argument(
        "--browser-origin",
        default="http://localhost:5173",
        help="Origem do portal já configurada na allowlist CORS da API",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Autoriza explicitamente criar fixtures em uma demo remota",
    )
    args = parser.parse_args()
    if (
        urlsplit(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
        and not args.allow_remote
    ):
        parser.error("Use --allow-remote para testar uma demo remota")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    admin_password = os.getenv("ROTALOG_DEMO_PASSWORD")
    if not admin_password:
        parser.error("Defina ROTALOG_DEMO_PASSWORD; não há senha padrão")
    suffix = uuid4().hex[:12]
    password = os.getenv("ROTALOG_SMOKE_ACCOUNT_PASSWORD") or secrets.token_urlsafe(32)
    if not 12 <= len(password) <= 1024:
        parser.error(
            "ROTALOG_SMOKE_ACCOUNT_PASSWORD deve ter de 12 a 1024 caracteres, quando configurada"
        )
    checks = []
    with httpx.Client(base_url=args.base_url, timeout=20) as client:

        def request(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code != expected:
                # Não imprime corpos de login/refresh nem credenciais.
                code = response.json().get("error", {}).get("code", "HTTP_ERROR")
                raise RuntimeError(
                    f"{method} {path}: {response.status_code} ({code}); esperado {expected}"
                )
            checks.append(f"{method} {path} -> {expected}")
            return response.json() if response.content else None

        def login(email, account_password):
            payload = request(
                "POST",
                "/api/v1/auth/login",
                data={"username": email, "password": account_password},
            )
            return {"Authorization": f"Bearer {payload['access_token']}"}, payload

        def account(alias, role):
            email = f"{alias}.{suffix}@demo.rotalog.invalid"
            user = request(
                "POST",
                "/api/v1/auth/register",
                expected=201,
                json={
                    "email": email,
                    "password": password,
                    "name": f"{alias} Sintético",
                    "organization_name": f"Empresa {alias} Sintética {suffix}",
                    "role": role,
                },
            )
            auth, _ = login(email, password)
            return auth, user

        request("GET", "/health")
        admin, _ = login("admin@demo.rotalog.invalid", admin_password)
        product = request(
            "POST",
            "/api/v1/admin/products",
            expected=201,
            headers=admin,
            json={
                "name": f"Pizza — mistura seca lacrada 400 g ({suffix})",
                "brand": "Marca Sintética",
                "category": "ALIMENTO_SECO_INDUSTRIALIZADO",
                "net_content": 400,
                "net_content_unit": "g",
                "packaging_type": "Pacote lacrado",
                "packaging_quantity": 1,
                "gross_weight_grams": 450,
                "volume_cm3": 1000,
                "variant_attributes": {"origem": "SIMULAÇÃO"},
            },
        )
        request(
            "POST",
            f"/api/v1/admin/products/{product['id']}/publish",
            headers=admin,
            json={"expected_version": product["version"]},
        )
        supplier, distributor = account("distribuidora", "SUPPLIER_OPERATOR")
        sid = distributor["organization_id"]
        warehouse = request(
            "POST",
            f"/api/v1/suppliers/{sid}/warehouses",
            expected=201,
            headers=supplier,
            json={
                "name": "Depósito Pizza Sintético",
                "address": "Endereço fictício da demonstração, Manaus",
                "opening_hours": "Seg–Sex 08:00–18:00",
                "region": "MANAUS",
            },
        )
        request(
            "POST",
            f"/api/v1/warehouses/{warehouse['id']}/geocoding-review",
            headers=admin,
            json={
                "expected_version": warehouse["version"],
                "latitude": "-3.100000",
                "longitude": "-60.000000",
                "reason": "Origem sintética; não é geocodificação real",
            },
        )
        offer = request(
            "POST",
            f"/api/v1/suppliers/{sid}/offers",
            expected=201,
            headers=supplier,
            json={
                "product_id": product["id"],
                "warehouse_id": warehouse["id"],
                "unit_price": "29.90",
                "currency": "BRL",
            },
        )
        request(
            "POST",
            "/api/v1/inventory/manual-adjustments",
            headers=supplier,
            json={
                "offer_id": offer["id"],
                "warehouse_id": warehouse["id"],
                "external_sellable_quantity": 20,
                "safety_stock_quantity": 0,
                "expected_version": 1,
                "reason": "Saldo inicial do teste HTTP sintético",
            },
        )
        now = datetime.now(UTC)
        capacity = request(
            "POST",
            "/api/v1/demo/capacity-slots",
            expected=201,
            headers=supplier,
            json={
                "warehouse_id": warehouse["id"],
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(days=1)).isoformat(),
                "total_quantity": 100,
            },
        )
        buyer, _ = account("comprador", "BUYER_ADMIN")
        supplier_ids = set()
        page = 1
        while True:
            suppliers = request(
                "GET",
                "/api/v1/suppliers",
                headers=buyer,
                params={"page": page, "page_size": 100},
            )
            supplier_ids.update(item["id"] for item in suppliers["items"])
            if page * 100 >= suppliers["total"]:
                break
            page += 1
        assert sid in supplier_ids
        request("GET", "/api/v1/products", headers=buyer, params={"search": suffix})
        request("GET", "/api/v1/offers", headers=buyer, params={"supplier_id": sid})
        checkout_headers = buyer | {"Idempotency-Key": f"checkout-{suffix}"}
        checkout_body = {
            "items": [{"offer_id": offer["id"], "quantity": 3}],
            "capacity_slot_id": capacity["id"],
        }
        checkout = request(
            "POST",
            "/api/v1/checkouts",
            expected=201,
            headers=checkout_headers,
            json=checkout_body,
        )
        assert (
            request(
                "POST",
                "/api/v1/checkouts",
                expected=201,
                headers=checkout_headers,
                json=checkout_body,
            )
            == checkout
        )
        path = f"/api/v1/checkouts/{checkout['id']}/confirm-payment"
        payment_headers = buyer | {"Idempotency-Key": f"payment-{suffix}"}
        paid = request(
            "POST",
            path,
            headers=payment_headers,
            json={"expected_version": checkout["version"]},
        )
        assert paid == request(
            "POST",
            path,
            headers=payment_headers,
            json={"expected_version": checkout["version"]},
        )
        assert paid["total"] == "89.70" and paid["provider"] == "SIMULATED"
        request("GET", "/api/v1/purchases", headers=buyer)
        orders = request("GET", "/api/v1/supplier-orders", headers=supplier)
        assert orders["total"] == 1
        order = orders["items"][0]
        assert order["id"] == paid["supplier_order_ids"][0]
        accepted = request(
            "POST",
            f"/api/v1/supplier-orders/{order['id']}/acceptance",
            headers=supplier | {"Idempotency-Key": f"acceptance-{suffix}"},
            json={
                "expected_version": order["version"],
                "lines": [
                    {"order_item_id": order["items"][0]["id"], "accepted_quantity": 3}
                ],
            },
        )
        separating = request(
            "POST",
            f"/api/v1/supplier-orders/{order['id']}/start-separation",
            headers=supplier,
            json={"expected_version": accepted["version"]},
        )
        fulfillment = request(
            "POST",
            f"/api/v1/supplier-orders/{order['id']}/fulfillments",
            expected=201,
            headers=supplier,
            json={
                "expected_version": separating["version"],
                "lines": [
                    {"order_item_id": order["items"][0]["id"], "allocated_quantity": 3}
                ],
            },
        )
        separated = request(
            "POST",
            f"/api/v1/fulfillments/{fulfillment['id']}/separation-results",
            headers=supplier,
            json={
                "expected_version": fulfillment["version"],
                "lines": [
                    {
                        "fulfillment_line_id": fulfillment["lines"][0]["id"],
                        "separated_quantity": 3,
                    }
                ],
            },
        )
        request(
            "POST",
            f"/api/v1/fulfillments/{fulfillment['id']}/mark-ready",
            headers=supplier,
            json={"expected_version": separated["version"]},
        )
        final = request("GET", f"/api/v1/supplier-orders/{order['id']}", headers=buyer)
        inventory = request(
            "GET",
            "/api/v1/inventory",
            headers=supplier,
            params={"offer_id": offer["id"]},
        )["items"][0]
        assert final["state"] == "READY_TO_DISPATCH"
        assert (
            inventory["effective_sellable_quantity"] == 17
            and inventory["rotalog_reserved_not_reflected_quantity"] == 3
        )
        if args.with_delivery or args.with_mobile or args.prepare_mobile:
            from smoke_delivery import exercise_delivery

            final, inventory = exercise_delivery(
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
                account_password=password,
                mobile=args.with_mobile,
                prepare_only=args.prepare_mobile,
            )
        unrelated, _ = login("fornecedor@demo.rotalog.invalid", admin_password)
        request(
            "GET",
            f"/api/v1/supplier-orders/{order['id']}",
            expected=404,
            headers=unrelated,
        )
        request(
            "POST",
            "/api/v1/admin/products",
            expected=403,
            headers=supplier,
            json={
                key: value
                for key, value in product.items()
                if key
                in {
                    "name",
                    "brand",
                    "category",
                    "gtin",
                    "net_content",
                    "net_content_unit",
                    "packaging_type",
                    "packaging_quantity",
                    "gross_weight_grams",
                    "volume_cm3",
                    "variant_attributes",
                }
            },
        )
        refreshed = request(
            "POST",
            "/api/v1/auth/refresh",
            json={
                "refresh_token": login(
                    f"comprador.{suffix}@demo.rotalog.invalid", password
                )[1]["refresh_token"]
            },
        )
        fresh_headers = {"Authorization": f"Bearer {refreshed['access_token']}"}
        request("POST", "/api/v1/auth/logout", expected=204, headers=fresh_headers)
        request("GET", "/api/v1/me", expected=401, headers=fresh_headers)
        browser = request(
            "POST",
            "/api/v1/auth/login",
            params={"client_type": "browser"},
            headers={"Origin": args.browser_origin},
            data={
                "username": f"comprador.{suffix}@demo.rotalog.invalid",
                "password": password,
            },
        )
        assert browser["refresh_token"] is None and browser["csrf_token"]
        request("POST", "/api/v1/auth/refresh", expected=403)
        browser_headers = {
            "Origin": args.browser_origin,
            "X-CSRF-Token": browser["csrf_token"],
        }
        request("POST", "/api/v1/auth/refresh", headers=browser_headers)
        request("POST", "/api/v1/auth/logout", expected=204, headers=browser_headers)
        request(
            "GET",
            "/api/v1/me",
            expected=401,
            headers={"Authorization": f"Bearer {browser['access_token']}"},
        )
        for origin, expected_status in [
            (args.browser_origin, 200),
            ("https://evil.invalid", 400),
        ]:
            preflight = client.options(
                "/api/v1/auth/refresh",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-CSRF-Token,Content-Type",
                },
            )
            assert preflight.status_code == expected_status
            assert ("access-control-allow-origin" in preflight.headers) == (
                expected_status == 200
            )
            checks.append(f"OPTIONS /api/v1/auth/refresh -> {expected_status}")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "is_synthetic": True,
                    "http_checks": len(checks),
                    "product": product["name"],
                    "total": paid["total"],
                    "currency": "BRL",
                    "payment_provider": "SIMULATED",
                    "order_state": final["state"],
                    "mobile_route_id": final.get("_mobile_route_id"),
                    "synthetic_accounts": {
                        "supplier_email": f"distribuidora.{suffix}@demo.rotalog.invalid",
                        "buyer_email": f"comprador.{suffix}@demo.rotalog.invalid",
                        "planner_email": f"planejador-{suffix}@demo.rotalog.invalid",
                        "driver_email": f"motorista-{suffix}@demo.rotalog.invalid",
                        "password_source": "ROTALOG_SMOKE_ACCOUNT_PASSWORD"
                        if os.getenv("ROTALOG_SMOKE_ACCOUNT_PASSWORD")
                        else "RANDOM_NOT_EXPORTED",
                    }
                    if args.prepare_mobile
                    else None,
                    "stock_total": inventory["external_sellable_quantity"],
                    "stock_reserved": inventory[
                        "rotalog_reserved_not_reflected_quantity"
                    ],
                    "stock_available": inventory["effective_sellable_quantity"],
                    "checks": checks,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
