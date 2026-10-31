"""Criação local e exclusiva do primeiro administrador fora da demonstração."""

import argparse
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy import select, text

import rotalog.core.models  # noqa: F401
from rotalog.catalog.models import Category
from rotalog.core.config import get_settings
from rotalog.core.contracts import fail
from rotalog.core.database import (
    create_database_engine,
    create_session_factory,
    transactional_session,
)
from rotalog.core.errors import RotaLogError
from rotalog.demo.models import DemoRun
from rotalog.demo.service import LIVE_CONTEXT_NAME
from rotalog.identity.models import Role, User
from rotalog.identity.schemas import DriverProvisionRequest
from rotalog.identity.security import password_hash, require_identity
from rotalog.identity.service import _insert_provisioned_user
from rotalog.organizations.service import create_organization


def bootstrap_admin(data, organization_name, factory, settings):
    require_identity(settings)
    if settings.demo_enabled:
        fail(
            "BOOTSTRAP_DEMO_FORBIDDEN",
            "Desabilite a demo para inicializar a identidade operacional.",
            409,
        )
    name = organization_name.strip()
    if not 1 <= len(name) <= 200:
        fail(
            "ORGANIZATION_NAME_INVALID",
            "Informe nome de organização entre 1 e 200 caracteres.",
            422,
        )
    hashed = password_hash.hash(data.password.get_secret_value())
    with transactional_session(factory) as session:
        session.execute(text("SELECT pg_advisory_xact_lock(20261031)"))
        run = (
            session.get(DemoRun, settings.data_context_id)
            if settings.data_context_id
            else session.scalar(
                select(DemoRun).where(DemoRun.name == LIVE_CONTEXT_NAME)
            )
        )
        if settings.data_context_id and run is None:
            fail(
                "DATA_CONTEXT_UNAVAILABLE", "Contexto configurado não encontrado.", 409
            )
        if run is None:
            run = DemoRun(name=LIVE_CONTEXT_NAME, is_synthetic=False)
            session.add(run)
            session.flush()
            session.add(
                Category(
                    demo_run_id=run.id,
                    code="ALIMENTO_SECO_INDUSTRIALIZADO",
                    name="Alimento seco industrializado",
                )
            )
        elif not run.is_active:
            fail(
                "DATA_CONTEXT_UNAVAILABLE",
                "Contexto operacional inválido ou encerrado.",
                409,
            )
        if session.scalar(
            select(User.id).where(
                User.demo_run_id == run.id,
                User.role == Role.ADMIN,
                User.is_synthetic.is_(False),
            )
        ):
            fail(
                "ADMIN_ALREADY_INITIALIZED",
                "Administrador inicial já existe; use provisionamento ou convite.",
                409,
            )
        organization = create_organization(
            session, name=name, demo_run_id=run.id, kind="INTERNAL"
        )
        return _insert_provisioned_user(
            session,
            email=data.email,
            name=data.name,
            hashed=hashed,
            role=Role.ADMIN,
            organization=organization,
            is_synthetic=False,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Lê a senha de stdin sem exibi-la; não recebe senha em argumento.",
    )
    args = parser.parse_args()
    password = (
        sys.stdin.readline().rstrip("\r\n")
        if args.password_stdin
        else getpass.getpass("Senha inicial: ")
    )
    if not args.password_stdin and password != getpass.getpass("Confirme a senha: "):
        parser.exit(1, "As senhas não coincidem.\n")
    try:
        data = DriverProvisionRequest(
            email=args.email, name=args.name, password=password
        )
    except ValidationError:
        parser.exit(
            1,
            "Email, nome ou senha inválidos; a senha deve ter de 12 a 1024 caracteres.\n",
        )
    settings = get_settings()
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        user = bootstrap_admin(
            data, args.organization_name, create_session_factory(engine), settings
        )
    except RotaLogError as exc:
        parser.exit(1, f"{exc.code}: {exc.message}\n")
    finally:
        engine.dispose()
    print(
        f"Administrador inicial criado: {user.id}; organização: {user.organization_id}."
    )


if __name__ == "__main__":
    main()
