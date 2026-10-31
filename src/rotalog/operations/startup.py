"""Aplica migrações e executa seed somente quando a demonstração está habilitada."""

import os

from alembic import command
from alembic.config import Config

from rotalog.core.config import get_settings
from rotalog.core.database import create_database_engine, create_session_factory


def main():
    settings = get_settings()
    command.upgrade(Config("alembic.ini"), "head")
    if settings.demo_enabled:
        from rotalog.demo.seed import seed_demo

        password = os.getenv("ROTALOG_DEMO_PASSWORD")
        if not password:
            raise RuntimeError(
                "ROTALOG_DEMO_PASSWORD é obrigatória para o seed de demonstração"
            )
        engine = create_database_engine(settings.database_url.get_secret_value())
        try:
            seed_demo(create_session_factory(engine), settings, password)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
