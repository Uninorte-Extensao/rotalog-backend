"""Testes do contrato padronizado de erros da API."""

import asyncio

from httpx import ASGITransport, AsyncClient, Response

from rotalog.api.main import create_app
from rotalog.core.config import Environment, Settings
from rotalog.core.errors import RotaLogError


async def _get(app, path: str) -> Response:
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


def test_expected_error_uses_canonical_envelope_and_request_id() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            environment=Environment.TEST,
            database_url="postgresql+psycopg://localhost/rotalog_test",
        )
    )

    @app.get("/api/v1/failure")
    async def failure() -> None:
        raise RotaLogError(
            code="TEST_CONFLICT",
            message="Conflito controlado.",
            status_code=409,
            details={"state": "test"},
        )

    response = asyncio.run(_get(app, "/api/v1/failure"))

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "TEST_CONFLICT",
            "message": "Conflito controlado.",
            "details": {"state": "test"},
            "request_id": response.headers["X-Request-Id"],
        }
    }


def test_framework_http_error_uses_canonical_envelope() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            environment=Environment.TEST,
            database_url="postgresql+psycopg://localhost/rotalog_test",
        )
    )

    response = asyncio.run(_get(app, "/missing"))

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "HTTP_ERROR",
            "message": "Not Found",
            "details": {},
            "request_id": response.headers["X-Request-Id"],
        }
    }
