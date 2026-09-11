"""Primitivas síncronas de persistência."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from rotalog.core.config import get_settings


class Base(DeclarativeBase):
    """Classe-base para os modelos SQLAlchemy 2.0."""


def create_database_engine(database_url: str) -> Engine:
    """Cria uma engine síncrona sem abrir conexão antecipadamente."""

    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria a fábrica de sessões curtas utilizada pelos casos de uso."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@lru_cache
def get_engine() -> Engine:
    """Retorna a engine configurada para o processo atual."""

    return create_database_engine(get_settings().database_url.get_secret_value())


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Retorna a fábrica de sessões configurada para o processo atual."""

    return create_session_factory(get_engine())


def get_db_session() -> Iterator[Session]:
    """Fornece uma sessão síncrona e garante seu fechamento."""

    with get_session_factory()() as session:
        yield session
