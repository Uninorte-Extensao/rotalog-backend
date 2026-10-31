"""Testes das configurações carregadas do ambiente."""

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from rotalog.core.config import Environment, Settings


def test_settings_reads_prefixed_environment_variables(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROTALOG_ENVIRONMENT", "test")
    monkeypatch.setenv("ROTALOG_DEBUG", "true")
    monkeypatch.setenv("ROTALOG_APP_NAME", "RotaLog Environment API")
    monkeypatch.setenv(
        "ROTALOG_DATABASE_URL",
        "postgresql+psycopg://localhost/rotalog_test",
    )

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.debug is True
    assert settings.app_name == "RotaLog Environment API"


def test_database_url_is_required(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("ROTALOG_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_url_requires_synchronous_psycopg() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        Settings(_env_file=None, database_url="sqlite+pysqlite:///:memory:")


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_demo_is_blocked_outside_local_and_test(environment: str) -> None:
    with pytest.raises(ValidationError, match="development ou test"):
        Settings(
            _env_file=None,
            environment=environment,
            demo_enabled=True,
            database_url="postgresql+psycopg://localhost/rotalog_test",
            jwt_secret="synthetic-key-for-tests-with-more-than-32-bytes",
        )


def test_demo_requires_signing_key(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("ROTALOG_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            _env_file=None,
            environment="test",
            demo_enabled=True,
            database_url="postgresql+psycopg://localhost/rotalog_test",
        )


def test_online_demo_requires_secure_cookie_and_exact_https_origins():
    base = {
        "_env_file": None,
        "environment": "demo",
        "demo_enabled": True,
        "database_url": "postgresql+psycopg://localhost/rotalog_test",
        "jwt_secret": "synthetic-test-signing-key-at-least-32-bytes",
    }
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(**base)
    for origins in (
        ["*"],
        ["http://portal.example.invalid"],
        ["https://portal.example.invalid/path"],
        ["https://user:pass@portal.example.invalid"],
    ):
        with pytest.raises(ValidationError):
            Settings(**base, cookie_secure=True, cors_origins=origins)
    with pytest.raises(ValidationError, match="debug"):
        Settings(**base, cookie_secure=True, debug=True)
    assert (
        Settings(
            **base, cookie_secure=True, cors_origins=["https://portal.example.invalid"]
        ).environment
        is Environment.DEMO
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"jwt_secret": None},
        {"cookie_secure": False},
        {"debug": True},
        {"cors_origins": ["http://portal.example.com"]},
    ],
)
def test_production_identity_requires_signing_key_https_and_no_debug(changes):
    base = {
        "_env_file": None,
        "environment": "production",
        "demo_enabled": False,
        "database_url": "postgresql+psycopg://localhost/rotalog_test",
        "jwt_secret": "production-test-secret-more-than-32-bytes",
        "cookie_secure": True,
        "cors_origins": ["https://portal.example.com"],
    }
    with pytest.raises(ValidationError):
        Settings(**(base | changes))
    assert Settings(**base, data_context_id="").data_context_id is None


def test_cross_site_cookie_requires_secure_transport():
    with pytest.raises(ValidationError, match="SameSite=None"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://localhost/rotalog_test",
            demo_enabled=False,
            cookie_samesite="none",
            cookie_secure=False,
        )
