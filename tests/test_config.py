"""Testes das configurações carregadas do ambiente."""

from pytest import MonkeyPatch

from rotalog.core.config import Environment, Settings


def test_settings_reads_prefixed_environment_variables(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROTALOG_ENVIRONMENT", "test")
    monkeypatch.setenv("ROTALOG_DEBUG", "true")
    monkeypatch.setenv("ROTALOG_APP_NAME", "RotaLog Environment API")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.debug is True
    assert settings.app_name == "RotaLog Environment API"
