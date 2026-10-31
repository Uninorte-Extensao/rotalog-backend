"""Casos de uso de identidade e autorização revalidada em cada transação."""

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from secrets import compare_digest, token_urlsafe
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rotalog.core.config import Settings
from rotalog.core.contracts import fail
from rotalog.core.database import transactional_session
from rotalog.demo.models import DemoRun
from rotalog.demo.service import identity_context, require_active_run
from rotalog.identity.models import (
    AuthSession,
    LoginThrottle,
    RefreshToken,
    Role,
    User,
    UserInvitation,
)
from rotalog.identity.schemas import (
    DriverProvisionRequest,
    PasswordChange,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from rotalog.identity.security import (
    create_access_token,
    csrf_token,
    decode_access_token,
    password_hash,
    require_identity,
    token_hash,
)
from rotalog.organizations.models import Membership, Organization
from rotalog.organizations.service import create_organization, grant_initial_membership

CAPABILITIES = {
    Role.BUYER: ["catalog:read", "checkout:create", "purchase:read"],
    Role.SUPPLIER: [
        "catalog:read",
        "offer:write",
        "inventory:write",
        "supplier_order:manage",
        "cargo:read",
        "cargo:resolve",
    ],
    Role.ADMIN: [
        "catalog:read",
        "product:write",
        "warehouse:review",
        "category:write",
        "user:provision",
        "invitation:manage",
    ],
    Role.DRIVER: [
        "route:read",
        "route:accept",
        "cargo:check",
        "cargo:read",
        "cargo:report",
        "cargo:receive",
        "route:start",
        "route:end",
        "route:complete",
        "tracking:read",
        "tracking:write",
        "delivery:execute",
    ],
    Role.PLANNER: [
        "route:read",
        "route:plan",
        "route:approve",
        "route:publish",
        "fleet:manage",
        "cargo:read",
        "tracking:read",
    ],
}


def _profile(
    session: Session,
    user: User,
    auth_session: AuthSession | None = None,
    organization_id: UUID | None = None,
) -> UserResponse:
    memberships = session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            Membership.user_id == user.id,
            Membership.is_active.is_(True),
            Organization.demo_run_id == user.demo_run_id,
        )
        .order_by(Membership.organization_id)
    ).all()
    selected = next(
        (
            row
            for row in memberships
            if row[0].organization_id == (organization_id or user.organization_id)
        ),
        None,
    )
    if selected is None:
        fail("MEMBERSHIP_REQUIRED", "Organização não autorizada.", 403)
    membership = selected[0]
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        organization_id=membership.organization_id,
        demo_run_id=user.demo_run_id,
        session_id=auth_session.id if auth_session else None,
        auth_version=user.auth_version,
        organizations=[
            {"id": str(org.id), "name": org.name, "role": member.role}
            for member, org in memberships
        ],
        capabilities=CAPABILITIES[Role(membership.role)],
        is_synthetic=user.is_synthetic,
        data_is_synthetic=session.get(DemoRun, user.demo_run_id).is_synthetic,
    )


def validate_email_mode(email, synthetic):
    if synthetic and not email.endswith(".invalid"):
        fail(
            "SYNTHETIC_EMAIL_REQUIRED",
            "A demonstração exige email terminado em .invalid.",
            422,
        )


def register(
    data: RegisterRequest, factory: sessionmaker[Session], settings: Settings
) -> UserResponse:
    require_identity(settings)
    validate_email_mode(data.email, settings.demo_enabled)
    hashed = password_hash.hash(data.password.get_secret_value())
    with transactional_session(factory) as session:
        run_id = identity_context(session, settings)
        organization = create_organization(
            session,
            name=data.organization_name,
            demo_run_id=run_id,
            kind="SUPPLIER" if data.role == Role.SUPPLIER else "BUYER",
        )
        user = session.scalar(
            insert(User)
            .values(
                email=data.email,
                name=data.name,
                password_hash=hashed,
                role=data.role,
                organization_id=organization.id,
                demo_run_id=run_id,
                is_synthetic=settings.demo_enabled,
            )
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User)
        )
        if user is None:
            fail("EMAIL_ALREADY_REGISTERED", "Email já cadastrado.")
        grant_initial_membership(
            session, user_id=user.id, organization_id=organization.id, role=data.role
        )
        return _profile(session, user)


def provision_driver(
    data: DriverProvisionRequest,
    actor: UserResponse,
    factory: sessionmaker[Session],
    settings: Settings | None = None,
) -> UserResponse:
    synthetic = settings.demo_enabled if settings else actor.is_synthetic
    validate_email_mode(data.email, synthetic)
    hashed = password_hash.hash(data.password.get_secret_value())
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER}, write=True)
        require_supplier_organization(session, actor)
        user = session.scalar(
            insert(User)
            .values(
                email=data.email,
                name=data.name,
                password_hash=hashed,
                role=Role.DRIVER,
                organization_id=actor.organization_id,
                demo_run_id=actor.demo_run_id,
                is_synthetic=synthetic,
            )
            .on_conflict_do_nothing(index_elements=[User.email])
            .returning(User)
        )
        if user is None:
            fail("EMAIL_ALREADY_REGISTERED", "Email já cadastrado.")
        grant_initial_membership(
            session,
            user_id=user.id,
            organization_id=actor.organization_id,
            role=Role.DRIVER,
        )
        return _profile(session, user)


def require_supplier_organization(session, actor):
    organization = session.scalar(
        select(Organization).where(
            Organization.id == actor.organization_id,
            Organization.demo_run_id == actor.demo_run_id,
            Organization.kind == "SUPPLIER",
        )
    )
    if organization is None:
        fail(
            "SUPPLIER_MEMBERSHIP_REQUIRED",
            "Selecione um vínculo com um fornecedor.",
            403,
        )
    return organization


def _provisioning_organization(session, organization_id, run_id, role):
    organization = session.scalar(
        select(Organization)
        .where(
            Organization.id == organization_id,
            Organization.demo_run_id == run_id,
        )
        .with_for_update()
    )
    if organization is None:
        fail("ORGANIZATION_NOT_FOUND", "Organização não encontrada.", 404)
    expected = "SUPPLIER" if role == Role.PLANNER else "INTERNAL"
    if organization.kind != expected:
        fail(
            "ORGANIZATION_ROLE_INVALID",
            f"Este papel exige organização {expected}.",
            422,
        )
    return organization


def _insert_provisioned_user(
    session, *, email, name, hashed, role, organization, is_synthetic=True
):
    user = session.scalar(
        insert(User)
        .values(
            email=email,
            name=name,
            password_hash=hashed,
            role=role,
            organization_id=organization.id,
            demo_run_id=organization.demo_run_id,
            is_synthetic=is_synthetic,
        )
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User)
    )
    if user is None:
        fail("EMAIL_ALREADY_REGISTERED", "Email já cadastrado.")
    grant_initial_membership(
        session, user_id=user.id, organization_id=organization.id, role=role
    )
    return _profile(session, user)


def provision_user(data, actor, factory, settings=None):
    synthetic = settings.demo_enabled if settings else actor.is_synthetic
    validate_email_mode(data.email, synthetic)
    hashed = password_hash.hash(data.password.get_secret_value())
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN}, write=True)
        organization = _provisioning_organization(
            session, data.organization_id, actor.demo_run_id, data.role
        )
        return _insert_provisioned_user(
            session,
            email=data.email,
            name=data.name,
            hashed=hashed,
            role=data.role,
            organization=organization,
            is_synthetic=synthetic,
        )


def create_invitation(data, actor, factory, settings=None):
    synthetic = settings.demo_enabled if settings else actor.is_synthetic
    validate_email_mode(data.email, synthetic)
    raw = token_urlsafe(48)
    now = datetime.now(UTC)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN}, write=True)
        _provisioning_organization(
            session, data.organization_id, actor.demo_run_id, data.role
        )
        if session.scalar(select(User.id).where(User.email == data.email)):
            fail("EMAIL_ALREADY_REGISTERED", "Email já cadastrado.")
        session.execute(
            update(UserInvitation)
            .where(
                UserInvitation.email == data.email,
                UserInvitation.organization_id == data.organization_id,
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        invitation = UserInvitation(
            email=data.email,
            role=data.role,
            organization_id=data.organization_id,
            demo_run_id=actor.demo_run_id,
            invited_by=actor.id,
            token_hash=token_hash(raw),
            expires_at=now + timedelta(hours=data.expires_in_hours),
            is_synthetic=synthetic,
        )
        session.add(invitation)
        session.flush()
        return {
            "id": invitation.id,
            "email": invitation.email,
            "role": invitation.role,
            "organization_id": invitation.organization_id,
            "expires_at": invitation.expires_at,
            "invitation_token": raw,
        }


def _require_open_invitation(invitation):
    if (
        invitation is None
        or invitation.accepted_at
        or invitation.revoked_at
        or invitation.expires_at <= datetime.now(UTC)
    ):
        fail("INVITATION_INVALID", "Convite inválido, expirado ou já utilizado.", 410)


def _identity_allowed(identity, settings):
    return (
        identity is not None
        and (not settings.demo_enabled or identity.is_synthetic)
        and (
            settings.data_context_id is None
            or identity.demo_run_id == settings.data_context_id
        )
    )


def accept_invitation(data, factory, settings):
    require_identity(settings)
    digest = token_hash(data.token.get_secret_value())
    # Recusa segredos inválidos antes do Argon2, sem manter locks durante o hash.
    with transactional_session(factory) as session:
        invitation = session.scalar(
            select(UserInvitation).where(UserInvitation.token_hash == digest)
        )
        _require_open_invitation(invitation)
        if not _identity_allowed(invitation, settings):
            fail("INVITATION_INVALID", "Convite inválido para este ambiente.", 410)
    hashed = password_hash.hash(data.password.get_secret_value())
    with transactional_session(factory) as session:
        # A organização é travada antes do convite, como na criação/revogação.
        invitation = session.scalar(
            select(UserInvitation).where(UserInvitation.token_hash == digest)
        )
        _require_open_invitation(invitation)
        if not _identity_allowed(invitation, settings):
            fail("INVITATION_INVALID", "Convite inválido para este ambiente.", 410)
        organization = _provisioning_organization(
            session, invitation.organization_id, invitation.demo_run_id, invitation.role
        )
        session.refresh(invitation, with_for_update=True)
        _require_open_invitation(invitation)
        require_active_run(session, invitation.demo_run_id)
        result = _insert_provisioned_user(
            session,
            email=invitation.email,
            name=data.name,
            hashed=hashed,
            role=invitation.role,
            organization=organization,
            is_synthetic=invitation.is_synthetic,
        )
        invitation.accepted_at = datetime.now(UTC)
        return result


def revoke_invitation(invitation_id, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN}, write=True)
        invitation = session.scalar(
            select(UserInvitation).where(
                UserInvitation.id == invitation_id,
                UserInvitation.demo_run_id == actor.demo_run_id,
            )
        )
        if invitation is None:
            fail("INVITATION_NOT_FOUND", "Convite não encontrado.", 404)
        session.scalar(
            select(Organization)
            .where(Organization.id == invitation.organization_id)
            .with_for_update()
        )
        session.refresh(invitation, with_for_update=True)
        if invitation.accepted_at:
            fail("INVITATION_ALREADY_ACCEPTED", "Convite já aceito.")
        invitation.revoked_at = datetime.now(UTC)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return password_hash.hash(token_urlsafe(32))


def _identity_scope(settings):
    conditions = []
    if settings.demo_enabled:
        conditions.append(User.is_synthetic.is_(True))
    if settings.data_context_id:
        conditions.append(User.demo_run_id == settings.data_context_id)
    return conditions


def _issue(
    session: Session, user: User, auth_session: AuthSession, settings: Settings
) -> TokenResponse:
    raw = token_urlsafe(48)
    session.add(RefreshToken(session_id=auth_session.id, token_hash=token_hash(raw)))
    return TokenResponse(
        access_token=create_access_token(user, auth_session, settings),
        refresh_token=raw,
        csrf_token=csrf_token(str(auth_session.id), settings)
        if auth_session.client_type == "browser"
        else None,
        user=_profile(session, user, auth_session),
    )


def login(
    email: str,
    password: str,
    factory: sessionmaker[Session],
    settings: Settings,
    client_type: str = "native",
    origin: str | None = None,
) -> TokenResponse:
    require_identity(settings)
    if client_type == "browser" and origin not in settings.cors_origins:
        fail("ORIGIN_NOT_ALLOWED", "Origem não autorizada.", 403)
    if not email or len(email) > 254 or not password or len(password) > 1024:
        fail("INVALID_CREDENTIALS", "Email ou senha inválidos.", 401)
    email = email.strip().lower()
    # Argon2 ocorre fora da transação de escrita e dos locks.
    with transactional_session(factory) as session:
        snapshot = session.execute(
            select(User.id, User.password_hash).where(
                User.email == email, *_identity_scope(settings)
            )
        ).first()
        throttle_snapshot = session.get(LoginThrottle, token_hash(email))
        if (
            throttle_snapshot
            and throttle_snapshot.blocked_until
            and throttle_snapshot.blocked_until > datetime.now(UTC)
        ):
            fail("LOGIN_RATE_LIMITED", "Aguarde antes de tentar novamente.", 429)
    hashed = snapshot.password_hash if snapshot else _dummy_hash()
    valid = password_hash.verify(password, hashed)
    error = None
    result = None
    with transactional_session(factory) as session:
        key = token_hash(email)
        session.execute(
            insert(LoginThrottle)
            .values(identifier=key, failures=0)
            .on_conflict_do_nothing()
        )
        throttle = session.scalar(
            select(LoginThrottle)
            .where(LoginThrottle.identifier == key)
            .with_for_update()
        )
        now = datetime.now(UTC)
        if throttle.blocked_until and throttle.blocked_until > now:
            error = ("LOGIN_RATE_LIMITED", "Aguarde antes de tentar novamente.", 429)
        else:
            user = session.scalar(
                select(User)
                .where(
                    User.email == email,
                    User.is_active.is_(True),
                    *_identity_scope(settings),
                )
                .with_for_update(read=True)
            )
            if user is None or not valid or user.password_hash != hashed:
                throttle.failures += 1
                if throttle.failures >= 5:
                    throttle.blocked_until = now + timedelta(
                        seconds=min(900, 30 * 2 ** min(throttle.failures - 5, 5))
                    )
                error = ("INVALID_CREDENTIALS", "Email ou senha inválidos.", 401)
            else:
                throttle.failures = 0
                throttle.blocked_until = None
                auth_session = AuthSession(
                    user_id=user.id,
                    expires_at=now + timedelta(days=30),
                    client_type=client_type,
                )
                session.add(auth_session)
                session.flush()
                result = _issue(session, user, auth_session, settings)
    if error:
        fail(*error)
    return result


def authenticate(
    token: str,
    factory: sessionmaker[Session],
    settings: Settings,
    organization_id: UUID | None = None,
) -> UserResponse:
    claims = decode_access_token(token, settings)
    try:
        user_id, session_id, run_id = (
            UUID(claims["sub"]),
            UUID(claims["session_id"]),
            UUID(claims["demo_run_id"]),
        )
    except (ValueError, TypeError, KeyError):
        fail("INVALID_TOKEN", "Token inválido.", 401)
    with transactional_session(factory) as session:
        user = session.get(User, user_id)
        auth_session = session.get(AuthSession, session_id)
        context = session.get(DemoRun, run_id)
        if (
            user is None
            or context is None
            or not _identity_allowed(user, settings)
            or not user.is_active
            or user.demo_run_id != run_id
            or user.auth_version != claims["auth_version"]
            or auth_session is None
            or auth_session.user_id != user.id
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= datetime.now(UTC)
        ):
            fail("INVALID_SESSION", "Sessão inválida ou revogada.", 401)
        return _profile(session, user, auth_session, organization_id)


def authorize(
    session: Session,
    actor: UserResponse,
    roles: set[Role] | None = None,
    *,
    write: bool = False,
) -> User:
    """Revalida usuário, sessão e vínculo sob locks mantidos até o commit."""
    user = session.scalar(
        select(User).where(User.id == actor.id).with_for_update(read=True)
    )
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == actor.session_id)
        .with_for_update(read=True)
    )
    if (
        not user
        or not user.is_active
        or user.demo_run_id != actor.demo_run_id
        or user.auth_version != actor.auth_version
        or not auth_session
        or auth_session.user_id != user.id
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= datetime.now(UTC)
    ):
        fail("INVALID_SESSION", "Sessão inválida ou revogada.", 401)
    context = session.get(DemoRun, user.demo_run_id)
    if (
        context is None
        or user.is_synthetic != actor.is_synthetic
        or context.is_synthetic != actor.data_is_synthetic
    ):
        fail("INVALID_SESSION", "Contexto de identidade inválido.", 401)
    membership = session.scalar(
        select(Membership)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(
            Membership.user_id == user.id,
            Membership.organization_id == actor.organization_id,
            Membership.is_active.is_(True),
            Organization.demo_run_id == user.demo_run_id,
        )
        .with_for_update(read=True, of=Membership)
    )
    if (
        not membership
        or membership.role != actor.role
        or (roles and membership.role not in roles)
    ):
        fail(
            "CAPABILITY_REQUIRED", "Permissão insuficiente para esta organização.", 403
        )
    if write:
        require_active_run(session, user.demo_run_id)
    return user


def refresh(
    raw: str,
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    browser: bool = False,
    origin: str | None = None,
    csrf: str | None = None,
    logout: bool = False,
) -> TokenResponse | None:
    require_identity(settings)
    error = None
    result = None
    with transactional_session(factory) as session:
        old = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
        )
        if old is None:
            fail("INVALID_REFRESH", "Refresh token inválido.", 401)
        # Ordem estável usuário -> sessão -> token, compartilhada com comandos protegidos.
        snapshot = session.get(AuthSession, old.session_id)
        user = session.scalar(
            select(User).where(User.id == snapshot.user_id).with_for_update(read=True)
        )
        if not _identity_allowed(user, settings):
            fail("INVALID_REFRESH", "Sessão inválida para este ambiente.", 401)
        auth_session = session.scalar(
            select(AuthSession)
            .where(AuthSession.id == old.session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        session.refresh(old)
        if auth_session.client_type == "browser":
            if (
                not browser
                or origin not in settings.cors_origins
                or not compare_digest(
                    csrf or "", csrf_token(str(auth_session.id), settings)
                )
            ):
                fail("CSRF_FAILED", "Origem ou token CSRF inválido.", 403)
        elif browser:
            fail("INVALID_REFRESH", "Canal de autenticação inválido.", 401)
        now = datetime.now(UTC)
        if old.used_at is not None:
            auth_session.revoked_at = now
            logging.getLogger(__name__).warning(
                "Refresh reutilizado; família de sessão revogada"
            )
            error = True
        elif (
            auth_session.revoked_at
            or auth_session.expires_at <= now
            or not user.is_active
        ):
            error = True
        elif logout:
            auth_session.revoked_at = now
        else:
            old.used_at = now
            result = _issue(session, user, auth_session, settings)
    if error:
        fail("INVALID_REFRESH", "Sessão inválida ou refresh reutilizado.", 401)
    return result


def revoke(
    actor: UserResponse,
    factory: sessionmaker[Session],
    *,
    all_sessions: bool = False,
    session_id: UUID | None = None,
) -> None:
    with transactional_session(factory) as session:
        # Escrita de sessão pega lock exclusivo do usuário antes dos locks de autorização.
        session.scalar(select(User).where(User.id == actor.id).with_for_update())
        authorize(session, actor)
        query = update(AuthSession).where(AuthSession.user_id == actor.id)
        if not all_sessions:
            query = query.where(AuthSession.id == (session_id or actor.session_id))
        result = session.execute(query.values(revoked_at=datetime.now(UTC)))
        if not result.rowcount:
            fail("SESSION_NOT_FOUND", "Sessão não encontrada.", 404)


def sessions(actor: UserResponse, factory: sessionmaker[Session]) -> list[dict]:
    with transactional_session(factory) as session:
        authorize(session, actor)
        return [
            {
                "id": item.id,
                "client_type": item.client_type,
                "created_at": item.created_at,
                "expires_at": item.expires_at,
                "revoked_at": item.revoked_at,
                "current": item.id == actor.session_id,
            }
            for item in session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == actor.id)
                .order_by(AuthSession.created_at)
            )
        ]


def change_password(
    data: PasswordChange, actor: UserResponse, factory: sessionmaker[Session]
) -> None:
    with transactional_session(factory) as session:
        user = session.get(User, actor.id)
        previous_hash = user.password_hash
    if not password_hash.verify(
        data.current_password.get_secret_value(), previous_hash
    ):
        fail("INVALID_CREDENTIALS", "Senha atual inválida.", 401)
    hashed = password_hash.hash(data.new_password.get_secret_value())
    with transactional_session(factory) as session:
        user = session.scalar(select(User).where(User.id == actor.id).with_for_update())
        authorize(session, actor, write=True)
        if user.password_hash != previous_hash:
            fail("VERSION_CONFLICT", "Credenciais alteradas; autentique novamente.")
        user.password_hash = hashed
        user.auth_version += 1
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id)
            .values(revoked_at=datetime.now(UTC))
        )
