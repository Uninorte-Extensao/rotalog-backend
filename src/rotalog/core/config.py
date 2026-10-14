"""Configuração da aplicação carregada a partir do ambiente."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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
    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Aceita somente a conexão síncrona PostgreSQL/psycopg da baseline."""

        raw_url = value.get_secret_value()
        try:
            driver_name = make_url(raw_url).drivername
        except ArgumentError as exc:
            raise ValueError("ROTALOG_DATABASE_URL inválida") from exc

        if driver_name != "postgresql+psycopg":
            raise ValueError(
                "ROTALOG_DATABASE_URL deve usar o driver postgresql+psycopg"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    """Retorna uma única instância imutável das configurações."""

    return Settings()
