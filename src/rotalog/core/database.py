"""Primitivas síncronas de persistência."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Engine, MetaData, Uuid, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from rotalog.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Classe-base para os modelos SQLAlchemy 2.0."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UuidPrimaryKeyMixin:
    """Chave pública UUID4 gerada também quando a escrita ocorre no banco."""

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


class TimestampMixin:
    """Instantes auditáveis armazenados como TIMESTAMPTZ em UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


def create_database_engine(database_url: str) -> Engine:
    """Cria uma engine síncrona sem abrir conexão antecipadamente."""

    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria a fábrica de sessões curtas utilizada pelos casos de uso."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def transactional_session(
    factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Delimita uma sessão e uma transação para exatamente um caso de uso."""

    active_factory = factory or get_session_factory()
    with active_factory() as session, session.begin():
        yield session


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
