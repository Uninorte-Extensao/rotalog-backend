"""Primitivas compartilhadas de persistência."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe-base para os modelos SQLAlchemy 2.0."""

