"""Expiração e fila privada de imagens sintéticas; sem scanner real do piloto."""

import argparse
import time

import rotalog.core.models  # noqa: F401 — registro completo também no processo isolado
from rotalog.core.config import get_settings
from rotalog.core.database import create_database_engine, create_session_factory
from rotalog.identity.security import require_identity
from rotalog.operations.file_service import process_product_image_files
from rotalog.ordering.service import expire_checkouts, expire_orders


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Executa um lote e encerra")
    args = parser.parse_args()
    settings = get_settings()
    require_identity(settings)
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        factory = create_session_factory(engine)
        while True:
            expire_checkouts(factory)
            expire_orders(factory)
            process_product_image_files(factory, settings)
            if args.once:
                break
            time.sleep(10)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
