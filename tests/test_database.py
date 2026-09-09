"""Testes das primitivas síncronas de persistência."""

from sqlalchemy.orm import Session

from rotalog.core.database import create_database_engine, create_session_factory


def test_session_factory_creates_bound_synchronous_session() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        assert isinstance(session, Session)
        assert session.bind is engine

    engine.dispose()
