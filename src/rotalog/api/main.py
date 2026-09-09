"""Bootstrap da aplicação FastAPI."""

from fastapi import FastAPI

from rotalog.api.health import router as health_router
from rotalog.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Cria e configura uma instância da API."""

    app_settings = settings or get_settings()
    application = FastAPI(
        title=app_settings.app_name,
        description=app_settings.app_description,
        version=app_settings.app_version,
        debug=app_settings.debug,
    )
    application.state.settings = app_settings
    application.include_router(health_router)
    return application


app = create_app()
