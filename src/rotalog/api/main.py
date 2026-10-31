"""Bootstrap da aplicação FastAPI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rotalog.api.exception_handler import (
    DEFAULT_ERROR_RESPONSES,
    register_exception_handlers,
)
from rotalog.api.health import router as health_router
from rotalog.api.middleware.request_id import RequestIDMiddleware
from rotalog.api.router import router as api_router
from rotalog.core.config import Settings, get_settings
from rotalog.core.database import create_database_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura uma instância da API."""

    app_settings = settings or get_settings()
    engine = create_database_engine(app_settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        description=app_settings.app_description,
        version=app_settings.app_version,
        debug=app_settings.debug,
        responses=DEFAULT_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.session_factory = create_session_factory(engine)
    if app_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Organization-Id",
                "X-Request-Id",
                "X-CSRF-Token",
                "Idempotency-Key",
                "X-Download-Token",
            ],
            expose_headers=["X-Request-Id"],
        )
    application.add_middleware(RequestIDMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
