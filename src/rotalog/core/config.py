"""Configuração da aplicação carregada a partir do ambiente."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Ambientes reconhecidos pela aplicação."""

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Configurações validadas provenientes de variáveis ``ROTALOG_*``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ROTALOG_",
        extra="ignore",
        frozen=True,
    )

    app_name: str = "RotaLog API"
    app_description: str = "API backend do marketplace B2B RotaLog."
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    database_url: SecretStr = SecretStr("postgresql+psycopg://localhost:5432/rotalog")


@lru_cache
def get_settings() -> Settings:
    """Retorna uma única instância imutável das configurações."""

    return Settings()
