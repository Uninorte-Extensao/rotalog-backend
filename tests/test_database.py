"""Testes das primitivas síncronas de persistência."""

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, select
from sqlalchemy.orm import Session

from rotalog.core.database import (
    create_database_engine,
    create_session_factory,
    transactional_session,
)


def test_session_factory_creates_bound_synchronous_session() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.bind is engine

    engine.dispose()


def test_transactional_session_commits_one_use_case() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    session_factory = create_session_factory(engine)
    counter = Table("counter", MetaData(), Column("value", Integer))
    counter.create(engine)

    with transactional_session(session_factory) as session:
        session.execute(counter.insert().values(value=1))

    with engine.connect() as connection:
        assert connection.scalar(select(counter.c.value)) == 1

    engine.dispose()


def test_transactional_session_rolls_back_failed_use_case() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    session_factory = create_session_factory(engine)
    counter = Table("counter", MetaData(), Column("value", Integer))
    counter.create(engine)

    with (
        pytest.raises(RuntimeError, match="falha no caso de uso"),
        transactional_session(session_factory) as session,
    ):
        session.execute(counter.insert().values(value=1))
        raise RuntimeError("falha no caso de uso")

    with engine.connect() as connection:
        assert connection.scalar(select(counter.c.value)) is None

    engine.dispose()
