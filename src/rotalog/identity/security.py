"""Argon2id e tokens: access JWT de 10 minutos e refresh opaco com hash."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from rotalog.core.config import Settings
from rotalog.core.contracts import fail
from rotalog.identity.models import AuthSession, User

password_hash = PasswordHash.recommended()
ACCESS_TOKEN_SECONDS = 600
ISSUER = "rotalog-api"
AUDIENCE = "rotalog-clients"


def require_demo(settings: Settings) -> str:
    if not settings.demo_enabled or settings.jwt_secret is None:
        fail("DEMO_DISABLED", "Demonstração não habilitada.", 403)
    return settings.jwt_secret.get_secret_value()


def require_identity(settings: Settings) -> str:
    if settings.jwt_secret is None:
        fail("AUTH_DISABLED", "Autenticação não configurada.", 403)
    return settings.jwt_secret.get_secret_value()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(session_id: str, settings: Settings) -> str:
    return hmac.new(
        require_identity(settings).encode(),
        f"csrf:{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


def create_access_token(
    user: User, auth_session: AuthSession, settings: Settings
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "session_id": str(auth_session.id),
            "auth_version": user.auth_version,
            "demo_run_id": str(user.demo_run_id),
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_TOKEN_SECONDS),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "jti": str(uuid4()),
        },
        require_identity(settings),
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> dict:
    secret = require_identity(settings)
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": [
                    "sub",
                    "session_id",
                    "auth_version",
                    "demo_run_id",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
        )
    except jwt.InvalidTokenError:
        fail("INVALID_TOKEN", "Token inválido ou expirado.", 401)
