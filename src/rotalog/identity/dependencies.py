"""Dependências FastAPI do módulo de identidade."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from rotalog.core.config import Settings
from rotalog.core.data_mode import synthetic_data
from rotalog.identity.schemas import UserResponse
from rotalog.identity.service import authenticate

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")


def application_settings(request: Request) -> Settings:
    return request.app.state.settings


def application_sessions(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


async def current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    factory: Annotated[sessionmaker[Session], Depends(application_sessions)],
    settings: Annotated[Settings, Depends(application_settings)],
    organization_id: Annotated[UUID | None, Header(alias="X-Organization-Id")] = None,
) -> UserResponse:
    actor = await run_in_threadpool(
        authenticate, token, factory, settings, organization_id
    )
    synthetic_data.set(actor.data_is_synthetic)
    return actor


CurrentUser = Annotated[UserResponse, Depends(current_user)]
SessionFactory = Annotated[sessionmaker[Session], Depends(application_sessions)]
ApplicationSettings = Annotated[Settings, Depends(application_settings)]
