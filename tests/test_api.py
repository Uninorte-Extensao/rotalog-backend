"""Testes do bootstrap e dos endpoints técnicos da API."""

import asyncio
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient, Response

from rotalog.api.main import create_app
from rotalog.core.config import Environment, Settings


def test_create_app_uses_validated_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_name="RotaLog Test API",
        app_version="9.9.9",
        environment=Environment.TEST,
        debug=True,
        database_url="postgresql+psycopg://localhost/rotalog_test",
    )

    app = create_app(settings)

    assert app.title == "RotaLog Test API"
    assert app.version == "9.9.9"
    assert app.debug is True
    assert app.state.settings is settings


def test_health_check_reports_process_availability() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            environment=Environment.TEST,
            database_url="postgresql+psycopg://localhost/rotalog_test",
        )
    )

    async def request_health(headers: dict[str, str] | None = None) -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get("/health", headers=headers)

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert UUID(response.headers["X-Request-Id"]).version == 4

    supplied_request_id = str(uuid4())
    response = asyncio.run(request_health({"X-Request-Id": supplied_request_id}))

    assert response.headers["X-Request-Id"] == supplied_request_id


def test_openapi_uses_canonical_error_contract() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            environment=Environment.TEST,
            database_url="postgresql+psycopg://localhost/rotalog_test",
        )
    )

    schema = app.openapi()

    assert "ErrorResponse" in schema["components"]["schemas"]
    assert schema["paths"]["/health"]["get"]["responses"]["422"]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
    }
