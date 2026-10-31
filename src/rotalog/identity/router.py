"""Contrato HTTP de autenticação; regras e transações ficam no serviço."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from rotalog.core.config import Settings
from rotalog.core.contracts import fail
from rotalog.identity import service
from rotalog.identity.dependencies import (
    ApplicationSettings,
    CurrentUser,
    SessionFactory,
)
from rotalog.identity.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationResponse,
    PasswordChange,
    RefreshRequest,
    RegisterRequest,
    SessionResponse,
    TokenResponse,
    UserProvisionRequest,
    UserResponse,
)

router = APIRouter(tags=["identidade"])
COOKIE = "rotalog_refresh"
COOKIE_PATH = "/api/v1/auth"


@router.post("/admin/users", response_model=UserResponse, status_code=201)
def provision_user(
    data: UserProvisionRequest,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
):
    return service.provision_user(data, actor, factory, settings)


@router.post("/admin/invitations", response_model=InvitationResponse, status_code=201)
def create_invitation(
    data: InvitationCreate,
    actor: CurrentUser,
    factory: SessionFactory,
    response: Response,
    settings: ApplicationSettings,
):
    response.headers["Cache-Control"] = "no-store"
    return service.create_invitation(data, actor, factory, settings)


@router.delete("/admin/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: UUID, actor: CurrentUser, factory: SessionFactory):
    service.revoke_invitation(invitation_id, actor, factory)


@router.post("/auth/invitations/accept", response_model=UserResponse, status_code=201)
def accept_invitation(
    data: InvitationAccept, factory: SessionFactory, settings: ApplicationSettings
):
    return service.accept_invitation(data, factory, settings)


def _deliver(
    result: TokenResponse, response: Response, settings: Settings, browser: bool
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    if browser:
        response.set_cookie(
            COOKIE,
            result.refresh_token,
            max_age=30 * 86400,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            path=COOKIE_PATH,
        )
        return result.model_copy(update={"refresh_token": None})
    return result


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=201,
    description="Cadastro de comprador/fornecedor em organização própria. Papéis administrativos são provisionados ou convidados.",
)
def register(
    data: RegisterRequest, factory: SessionFactory, settings: ApplicationSettings
) -> UserResponse:
    return service.register(data, factory, settings)


@router.post("/auth/login", response_model=TokenResponse)
def login(
    request: Request,
    response: Response,
    credentials: Annotated[OAuth2PasswordRequestForm, Depends()],
    factory: SessionFactory,
    settings: ApplicationSettings,
    client_type: Literal["native", "browser"] = "native",
) -> TokenResponse:
    result = service.login(
        credentials.username,
        credentials.password,
        factory,
        settings,
        client_type,
        request.headers.get("origin"),
    )
    return _deliver(result, response, settings, client_type == "browser")


def _refresh_credentials(
    data: RefreshRequest | None, request: Request
) -> tuple[str, bool]:
    # Não permite escolher silenciosamente entre credenciais conflitantes.
    body_token = (
        data.refresh_token.get_secret_value() if data and data.refresh_token else None
    )
    cookie = request.cookies.get(COOKIE)
    if (body_token is None) == (cookie is None):
        fail("REFRESH_REQUIRED", "Informe um refresh token por corpo OU cookie.", 401)
    return body_token or cookie, cookie is not None


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    factory: SessionFactory,
    settings: ApplicationSettings,
    data: RefreshRequest | None = None,
) -> TokenResponse:
    raw, browser = _refresh_credentials(data, request)
    result = service.refresh(
        raw,
        factory,
        settings,
        browser=browser,
        origin=request.headers.get("origin"),
        csrf=request.headers.get("x-csrf-token"),
    )
    return _deliver(result, response, settings, browser)


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    factory: SessionFactory,
    settings: ApplicationSettings,
    data: RefreshRequest | None = None,
) -> None:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        actor = service.authenticate(authorization[7:], factory, settings)
        service.revoke(actor, factory)
    else:
        raw, browser = _refresh_credentials(data, request)
        service.refresh(
            raw,
            factory,
            settings,
            browser=browser,
            origin=request.headers.get("origin"),
            csrf=request.headers.get("x-csrf-token"),
            logout=True,
        )
    response.delete_cookie(
        COOKIE,
        path=COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/auth/logout-all", status_code=204)
def logout_all(actor: CurrentUser, factory: SessionFactory) -> None:
    service.revoke(actor, factory, all_sessions=True)


@router.get("/me", response_model=UserResponse)
def me(actor: CurrentUser) -> UserResponse:
    return actor


@router.get("/me/sessions", response_model=list[SessionResponse])
def sessions(actor: CurrentUser, factory: SessionFactory) -> list[dict]:
    return service.sessions(actor, factory)


@router.delete("/me/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: UUID, actor: CurrentUser, factory: SessionFactory
) -> None:
    service.revoke(actor, factory, session_id=session_id)


@router.post("/auth/password/change", status_code=204)
def password_change(
    data: PasswordChange, actor: CurrentUser, factory: SessionFactory
) -> None:
    service.change_password(data, actor, factory)
