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
