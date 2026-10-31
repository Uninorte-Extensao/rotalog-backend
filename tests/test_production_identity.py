"""Identidade fora da demo, com dados de teste compartilhados ou contexto novo."""

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_catalog_ordering import PRODUCT, login, ok

from rotalog.api.main import create_app
from rotalog.core.config import Settings
from rotalog.core.database import transactional_session
from rotalog.core.errors import RotaLogError
from rotalog.demo.fixtures import DEMO_RUN_ID
from rotalog.demo.models import DemoRun
from rotalog.identity.bootstrap import bootstrap_admin
from rotalog.identity.models import User
from rotalog.identity.schemas import DriverProvisionRequest
from rotalog.organizations.models import Membership

PASSWORD = "production-auth-only-test-password"


def production_settings(database_url, tmp_path, *, context_id=None):
    return Settings(
        _env_file=None,
        database_url=database_url,
        environment="production",
        demo_enabled=False,
        jwt_secret="test-only-jwt-secret-with-at-least-32-bytes",
        cookie_secure=True,
        cors_origins=["https://portal.example.com"],
        data_context_id=context_id,
        product_images_root=tmp_path / "private-images",
    )


def initial_admin(factory, settings):
    return bootstrap_admin(
        DriverProvisionRequest(
            email="admin@example.com", name="Admin de teste", password=PASSWORD
        ),
        "Operação de teste",
        factory,
        settings,
    )


@pytest.fixture
def production_client(demo_client, database_url, tmp_path):
    settings = production_settings(database_url, tmp_path, context_id=DEMO_RUN_ID)
    app = create_app(settings)
    initial_admin(app.state.session_factory, settings)
    with TestClient(app, base_url="https://api.example.com") as client:
        yield client


def real_login(client, email="admin@example.com", *, password=PASSWORD, browser=False):
    return client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        params={"client_type": "browser" if browser else "native"},
        headers={"Origin": "https://portal.example.com"} if browser else {},
    )


def auth(client):
    result = ok(real_login(client))
    return {"Authorization": "Bearer " + result["access_token"]}, result


def test_real_identity_and_existing_test_accounts_share_dataset(production_client):
    headers, result = auth(production_client)
    assert result["user"]["is_synthetic"] is False
    assert result["user"]["data_is_synthetic"] is True
    assert result["user"]["demo_run_id"] == str(DEMO_RUN_ID)
    assert result["user"]["organizations"][0]["role"] == "ROTALOG_ADMIN"
    products = ok(production_client.get("/api/v1/products", headers=headers))
    assert products["total"] == 4 and products["is_synthetic"] is True
    assert all(item["is_synthetic"] for item in products["items"])
    seed_headers, _ = login(production_client, "fornecedor")
    assert (
        production_client.get("/api/v1/inventory", headers=seed_headers).status_code
        == 200
    )
    with pytest.raises(RotaLogError, match="já existe"):
        initial_admin(
            production_client.app.state.session_factory,
            production_client.app.state.settings,
        )


def test_production_public_registration_and_privileged_provisioning(production_client):
    admin, admin_token = auth(production_client)
    supplier = ok(
        production_client.post(
            "/api/v1/auth/register",
            json={
                "email": "supplier@example.com",
                "name": "Fornecedor",
                "organization_name": "Distribuidora",
                "password": PASSWORD,
                "role": "SUPPLIER_OPERATOR",
            },
        ),
        201,
    )
    assert supplier["is_synthetic"] is False
    supplier_token = ok(real_login(production_client, "supplier@example.com"))
    supplier_headers = {"Authorization": "Bearer " + supplier_token["access_token"]}
    for role in ("ROTALOG_ADMIN", "ROUTE_PLANNER"):
        assert (
            production_client.post(
                "/api/v1/auth/register",
                json={
                    "email": "privileged@example.com",
                    "name": "Inválido",
                    "organization_name": "Org",
                    "password": PASSWORD,
                    "role": role,
                },
            ).status_code
            == 422
        )
    body = {
        "email": "planner@example.com",
        "name": "Planejador",
        "password": PASSWORD,
        "organization_id": supplier["organization_id"],
        "role": "ROUTE_PLANNER",
    }
    assert (
        production_client.post(
            "/api/v1/admin/users", headers=supplier_headers, json=body
        ).status_code
        == 403
    )
    planner = ok(
        production_client.post("/api/v1/admin/users", headers=admin, json=body), 201
    )
    assert planner["is_synthetic"] is False
    planner_token = ok(real_login(production_client, "planner@example.com"))
    planner_headers = {"Authorization": "Bearer " + planner_token["access_token"]}
    driver = ok(
        production_client.post(
            "/api/v1/drivers",
            headers=planner_headers,
            json={
                "email": "driver@example.com",
                "name": "Motorista",
                "password": PASSWORD,
            },
        ),
        201,
    )
    assert (
        driver["organization_id"] == supplier["organization_id"]
        and driver["is_synthetic"] is False
    )
    ok(real_login(production_client, "driver@example.com"))
    assert {
        d["id"]
        for d in ok(production_client.get("/api/v1/drivers", headers=planner_headers))
    } == {driver["id"]}
    ok(
        production_client.get(
            "/api/v1/route-planning/eligible-fulfillments", headers=planner_headers
        )
    )
    administrator = body | {
        "email": "another.admin@example.com",
        "role": "ROTALOG_ADMIN",
        "organization_id": admin_token["user"]["organization_id"],
    }
    ok(
        production_client.post(
            "/api/v1/admin/users", headers=admin, json=administrator
        ),
        201,
    )
    ok(real_login(production_client, "another.admin@example.com"))


def test_production_invitation_and_demo_boundary(production_client, demo_client):
    admin, _ = auth(production_client)
    _, supplier = login(production_client, "fornecedor")
    invite = ok(
        production_client.post(
            "/api/v1/admin/invitations",
            headers=admin,
            json={
                "email": "invited.planner@example.com",
                "role": "ROUTE_PLANNER",
                "organization_id": supplier["organization_id"],
            },
        ),
        201,
    )
    body = {
        "token": invite["invitation_token"],
        "name": "Planejador convidado",
        "password": PASSWORD,
    }
    assert (
        demo_client.post("/api/v1/auth/invitations/accept", json=body).status_code
        == 410
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: production_client.post(
                    "/api/v1/auth/invitations/accept", json=body
                ),
                range(2),
            )
        )
    assert sorted(response.status_code for response in responses) == [201, 410]
    result = ok(real_login(production_client, "invited.planner@example.com"))
    assert result["user"]["is_synthetic"] is False
    assert (
        demo_client.get(
            "/api/v1/me", headers={"Authorization": "Bearer " + result["access_token"]}
        ).status_code
        == 401
    )
    assert (
        demo_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": result["refresh_token"]}
        ).status_code
        == 401
    )
    assert real_login(demo_client, "invited.planner@example.com").status_code == 401


def test_production_refresh_sessions_revocation_and_password_change(production_client):
    headers, result = auth(production_client)
    refreshed = ok(
        production_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": result["refresh_token"]}
        )
    )
    assert refreshed["user"]["is_synthetic"] is False
    assert len(ok(production_client.get("/api/v1/me/sessions", headers=headers))) == 1
    assert (
        production_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": result["refresh_token"]}
        ).status_code
        == 401
    )
    assert production_client.get("/api/v1/me", headers=headers).status_code == 401
    headers, _ = auth(production_client)
    assert (
        production_client.post(
            "/api/v1/auth/password/change",
            headers=headers,
            json={
                "current_password": PASSWORD,
                "new_password": PASSWORD + "-updated",
            },
        ).status_code
        == 204
    )
    assert production_client.get("/api/v1/me", headers=headers).status_code == 401
    assert real_login(production_client).status_code == 401
    result = ok(real_login(production_client, password=PASSWORD + "-updated"))
    headers = {"Authorization": "Bearer " + result["access_token"]}
    assert (
        production_client.post("/api/v1/auth/logout-all", headers=headers).status_code
        == 204
    )
    assert production_client.get("/api/v1/me", headers=headers).status_code == 401


def test_production_browser_cookies_csrf_and_patch_cors(production_client):
    response = real_login(production_client, browser=True)
    result = ok(response)
    assert result["refresh_token"] is None
    assert (
        "Secure" in response.headers["set-cookie"]
        and "HttpOnly" in response.headers["set-cookie"]
    )
    assert production_client.post("/api/v1/auth/refresh").status_code == 403
    headers = {
        "Origin": "https://portal.example.com",
        "X-CSRF-Token": result["csrf_token"],
    }
    ok(production_client.post("/api/v1/auth/refresh", headers=headers))
    preflight = production_client.options(
        "/api/v1/admin/products/00000000-0000-4000-8000-000000000000",
        headers={
            "Origin": "https://portal.example.com",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert (
        preflight.status_code == 200
        and "PATCH" in preflight.headers["access-control-allow-methods"]
    )
    logout = production_client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204 and "Max-Age=0" in logout.headers["set-cookie"]


def test_cross_site_cookie_keeps_origin_and_csrf_protection(production_client):
    settings = production_client.app.state.settings.model_copy(
        update={"cookie_samesite": "none"}
    )
    with TestClient(create_app(settings), base_url="https://api.example.com") as client:
        response = real_login(client, browser=True)
        result = ok(response)
        assert (
            "SameSite=none" in response.headers["set-cookie"]
            and "Secure" in response.headers["set-cookie"]
        )
        headers = {
            "Origin": "https://evil.example.org",
            "X-CSRF-Token": result["csrf_token"],
        }
        assert client.post("/api/v1/auth/refresh", headers=headers).status_code == 403
        headers["Origin"] = "https://portal.example.com"
        ok(client.post("/api/v1/auth/refresh", headers=headers))
        logout = client.post("/api/v1/auth/logout", headers=headers)
        assert (
            logout.status_code == 204
            and "SameSite=none" in logout.headers["set-cookie"]
        )


def test_production_flow_keeps_simulated_payments_and_delivery(production_client):
    from test_delivery import test_delivery_end_to_end_and_private_proof

    test_delivery_end_to_end_and_private_proof(production_client)
    admin, _ = auth(production_client)
    assert (
        ok(production_client.get("/api/v1/admin/products", headers=admin))[
            "is_synthetic"
        ]
        is True
    )


def test_fresh_production_context_without_demo_seed(
    alembic_config, database_url, tmp_path
):
    command.upgrade(alembic_config, "head")
    settings = production_settings(database_url, tmp_path)
    app = create_app(settings)
    initial_admin(app.state.session_factory, settings)
    with TestClient(app, base_url="https://api.example.com") as client:
        headers, result = auth(client)
        assert (
            result["user"]["is_synthetic"] is False
            and result["user"]["data_is_synthetic"] is False
        )
        product = ok(
            client.post("/api/v1/admin/products", headers=headers, json=PRODUCT), 201
        )
        assert product["is_synthetic"] is False
        listed = ok(client.get("/api/v1/admin/products", headers=headers))
        assert (
            listed["is_synthetic"] is False
            and listed["items"][0]["is_synthetic"] is False
        )
        assert listed["label"] == "Dados operacionais"
    command.check(alembic_config)
    with pytest.raises(RuntimeError, match="preserve"):
        command.downgrade(alembic_config, "20261031_0010")
    with transactional_session(app.state.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(DemoRun)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 1


def test_closed_or_inactive_membership_is_revalidated(production_client):
    headers, result = auth(production_client)
    with transactional_session(production_client.app.state.session_factory) as session:
        membership = session.scalar(
            select(Membership).where(Membership.user_id == UUID(result["user"]["id"]))
        )
        membership.is_active = False
    assert production_client.get("/api/v1/me", headers=headers).status_code == 403


def test_explicit_context_limits_login_token_and_refresh(production_client):
    headers, token = auth(production_client)
    settings = production_client.app.state.settings.model_copy(
        update={"data_context_id": uuid4()}
    )
    with TestClient(create_app(settings), base_url="https://api.example.com") as other:
        assert real_login(other).status_code == 401
        assert other.get("/api/v1/me", headers=headers).status_code == 401
        assert (
            other.post(
                "/api/v1/auth/refresh", json={"refresh_token": token["refresh_token"]}
            ).status_code
            == 401
        )
    assert production_client.get("/api/v1/me", headers=headers).status_code == 200


def test_context_flags_do_not_leak_between_concurrent_requests(
    demo_client, database_url, tmp_path
):
    settings = production_settings(database_url, tmp_path)
    app = create_app(settings)
    initial_admin(app.state.session_factory, settings)
    demo_auth, _ = login(demo_client)
    with TestClient(app, base_url="https://api.example.com") as real:
        real_auth, _ = auth(real)
        ok(real.post("/api/v1/admin/products", headers=real_auth, json=PRODUCT), 201)

        def view(index):
            client, headers, synthetic = (
                (demo_client, demo_auth, True)
                if index % 2
                else (real, real_auth, False)
            )
            response = ok(client.get("/api/v1/admin/products", headers=headers))
            assert response["is_synthetic"] is synthetic
            assert all(p["is_synthetic"] is synthetic for p in response["items"])
            return response["total"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            totals = list(executor.map(view, range(12)))
        assert totals == [1, 4] * 6


def test_bootstrap_concurrency_creates_only_one_admin(
    alembic_config, database_url, tmp_path
):
    command.upgrade(alembic_config, "head")
    settings = production_settings(database_url, tmp_path)
    app = create_app(settings)

    def create(_):
        try:
            return initial_admin(app.state.session_factory, settings).role
        except RotaLogError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, range(2)))
    assert sorted(results) == ["ADMIN_ALREADY_INITIALIZED", "ROTALOG_ADMIN"]
    with transactional_session(app.state.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(Membership)) == 1
