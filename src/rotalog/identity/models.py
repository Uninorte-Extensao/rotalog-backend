"""Modelos persistentes do módulo de identidade."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from rotalog.core.database import Base, TimestampMixin, UuidPrimaryKeyMixin


class Role(StrEnum):
    BUYER = "BUYER_ADMIN"
    SUPPLIER = "SUPPLIER_OPERATOR"
    DRIVER = "DRIVER"
    ADMIN = "ROTALOG_ADMIN"
    PLANNER = "ROUTE_PLANNER"


class User(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "identity_user"
    __table_args__ = (
        CheckConstraint(
            "role IN ('BUYER_ADMIN', 'SUPPLIER_OPERATOR', 'DRIVER', 'ROTALOG_ADMIN', 'ROUTE_PLANNER')",
            name="role_valid",
        ),
    )

    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    organization_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        index=True,
    )
    demo_run_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("demo_run.id", ondelete="RESTRICT"),
        index=True,
    )


class LoginThrottle(Base):
    """Bloqueio persistente por identificador, inclusive para emails inexistentes."""

    __tablename__ = "login_throttle"
    __table_args__ = (CheckConstraint("failures >= 0", name="failures_non_negative"),)

    identifier: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auth_session"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_user.id", ondelete="RESTRICT"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_type: Mapped[str] = mapped_column(String(20))


class RefreshToken(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "refresh_token"
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_session.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserInvitation(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_invitation"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ROTALOG_ADMIN', 'ROUTE_PLANNER')", name="role_valid"
        ),
    )
    email: Mapped[str] = mapped_column(String(254), index=True)
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    role: Mapped[str] = mapped_column(String(32))
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"))
    demo_run_id: Mapped[UUID] = mapped_column(ForeignKey("demo_run.id"))
    invited_by: Mapped[UUID] = mapped_column(ForeignKey("identity_user.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
