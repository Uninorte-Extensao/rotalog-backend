"""Configuração da aplicação carregada a partir do ambiente."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Environment(StrEnum):
    """Ambientes reconhecidos pela aplicação."""

    DEVELOPMENT = "development"
    TEST = "test"
    DEMO = "demo"
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
        hide_input_in_errors=True,
    )

    app_name: str = "RotaLog API"
    app_description: str = "API backend do marketplace B2B RotaLog."
    app_version: str = "0.2.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    database_url: SecretStr
    demo_enabled: bool = False
    data_context_id: UUID | None = None
    jwt_secret: SecretStr | None = None
    cors_origins: list[str] = []
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    product_images_root: Path = Path(".local-runtime/product-images")
    product_images_per_product: int = Field(default=5, ge=1, le=20)
    product_image_max_bytes: int = Field(
        default=8 * 1024 * 1024, ge=1, le=8 * 1024 * 1024
    )
    product_image_max_pixels: int = Field(default=16_000_000, ge=1, le=16_000_000)
    product_image_upload_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    tracking_stale_seconds: int = Field(default=120, ge=30, le=3600)
    tracking_sync_window_seconds: int = Field(default=86400, ge=60, le=86400)
    tracking_accuracy_warning_meters: int = Field(default=100, ge=1, le=10000)
    tracking_batches_per_minute: int = Field(default=20, ge=1, le=120)

    @field_validator("data_context_id", mode="before")
    @classmethod
    def empty_data_context(cls, value):
        return None if value == "" else value

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def empty_jwt_secret(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        return None if value == "" else value

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < 32:
            raise ValueError("ROTALOG_JWT_SECRET deve ter pelo menos 32 bytes")
        return value

    @model_validator(mode="after")
    def validate_demo_environment(self) -> "Settings":
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None exige ROTALOG_COOKIE_SECURE=true e HTTPS")
        if self.demo_enabled and self.environment not in {
            Environment.DEVELOPMENT,
            Environment.TEST,
            Environment.DEMO,
        }:
            raise ValueError(
                "A demo só pode ser habilitada em development ou test ou demo"
            )
        if self.demo_enabled and self.jwt_secret is None:
            raise ValueError("ROTALOG_JWT_SECRET é obrigatória para habilitar a demo")
        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if self.jwt_secret is None:
                raise ValueError(
                    "ROTALOG_JWT_SECRET é obrigatória fora da demonstração"
                )
            if not self.cookie_secure:
                raise ValueError("Produção exige ROTALOG_COOKIE_SECURE=true e HTTPS")
            if self.debug:
                raise ValueError("Produção não permite debug")
        if self.environment is Environment.DEMO and not self.cookie_secure:
            raise ValueError("Demo online exige ROTALOG_COOKIE_SECURE=true e HTTPS")
        if self.environment is Environment.DEMO and self.debug:
            raise ValueError("Demo online não permite debug")
        for origin in self.cors_origins:
            from urllib.parse import urlsplit

            url = urlsplit(origin)
            if (
                url.scheme not in {"http", "https"}
                or not url.netloc
                or url.username is not None
                or url.password is not None
                or url.path
                or url.query
                or url.fragment
                or "*" in origin
            ):
                raise ValueError("CORS exige origens exatas, sem caminhos ou curingas")
            if (
                self.environment
                in {Environment.DEMO, Environment.STAGING, Environment.PRODUCTION}
                and url.scheme != "https"
            ):
                raise ValueError("Ambiente online exige origens CORS HTTPS")
        return self

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
