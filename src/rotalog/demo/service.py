"""Validação pública da execução sintética dentro de cada transação de escrita."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rotalog.core.errors import RotaLogError
from rotalog.demo.fixtures import DEMO_RUN_ID
from rotalog.demo.models import DemoRun

LIVE_CONTEXT_NAME = "rotalog-live"


def identity_context(session, settings):
    if settings.demo_enabled:
        return require_active_run(session)
    if settings.data_context_id:
        return require_active_run(session, settings.data_context_id)
    run_id = session.scalar(
        select(DemoRun.id).where(
            DemoRun.name == LIVE_CONTEXT_NAME,
            DemoRun.is_synthetic.is_(False),
            DemoRun.is_active.is_(True),
        )
    )
    if run_id is None:
        raise RotaLogError(
            code="IDENTITY_NOT_INITIALIZED",
            message="Inicialize o primeiro administrador pelo comando de bootstrap.",
            status_code=409,
        )
    return require_active_run(session, run_id)


def require_active_run(session: Session, run_id: UUID = DEMO_RUN_ID) -> UUID:
    """Mantém a execução ativa até o commit, sem serializar escritas independentes."""
    active_id = session.scalar(
        select(DemoRun.id)
        .where(DemoRun.id == run_id, DemoRun.is_active.is_(True))
        .with_for_update(read=True)
    )
    if active_id is None:
        raise RotaLogError(
            code="DATA_CONTEXT_UNAVAILABLE"
            if run_id != DEMO_RUN_ID
            else "DEMO_UNAVAILABLE",
            message="Contexto de dados indisponível ou encerrado.",
            status_code=409,
        )
    return active_id
