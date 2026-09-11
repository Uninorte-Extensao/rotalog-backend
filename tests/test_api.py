"""Testes do bootstrap e dos endpoints técnicos da API."""

import asyncio

from httpx import ASGITransport, AsyncClient

from rotalog.api.main import create_app
from rotalog.core.config import Environment, Settings


def test_create_app_uses_validated_settings() -> None:
    settings = Settings(
        _env_file=None,
        app_name="RotaLog Test API",
        app_version="9.9.9",
        environment=Environment.TEST,
        debug=True,
    )

    app = create_app(settings)

    assert app.title == "RotaLog Test API"
    assert app.version == "9.9.9"
    assert app.debug is True
    assert app.state.settings is settings


def test_health_check_reports_process_availability() -> None:
    app = create_app(Settings(_env_file=None, environment=Environment.TEST))

    async def request_health() -> tuple[int, dict[str, str]]:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/health")
            return response.status_code, response.json()

    status_code, payload = asyncio.run(request_health())

    assert status_code == 200
    assert payload == {"status": "ok"}
