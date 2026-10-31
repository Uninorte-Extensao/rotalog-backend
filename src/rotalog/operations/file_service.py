"""Arquivos privados por finalidade; validação sintética fora das transações."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text

from rotalog.core.contracts import fail
from rotalog.core.database import transactional_session
from rotalog.core.errors import RotaLogError
from rotalog.demo.models import DemoRun
from rotalog.demo.service import require_active_run
from rotalog.identity.models import Role
from rotalog.identity.security import require_identity, token_hash
from rotalog.identity.service import authorize
from rotalog.operations.file_models import FileDownloadGrant, FileEvent, StoredFile
from rotalog.operations.file_schemas import FileStatus
from rotalog.operations.file_storage import PrivateImageStorage, inspect_product_image
from rotalog.operations.service import begin_idempotency, finish_idempotency


def _event(session, item, kind, actor=None):
    session.add(
        FileEvent(file_id=item.id, kind=kind, actor_id=actor.id if actor else None)
    )


def _status(item):
    return FileStatus(
        file_id=item.id,
        purpose=item.purpose,
        product_id=item.product_id,
        attempt_id=item.attempt_id,
        route_id=item.route_id,
        status=item.status,
        size_bytes=item.size_bytes,
        mime_type=item.mime_type,
        sha256=item.sha256,
        expires_at=item.expires_at,
        rejection_reason=item.rejection_reason,
    ).model_dump(mode="json")


def _owned_file(session, file_id, actor, *, lock=False):
    query = select(StoredFile).where(
        StoredFile.id == file_id,
        StoredFile.demo_run_id == actor.demo_run_id,
        StoredFile.organization_id == actor.organization_id,
    )
    item = session.scalar(query.with_for_update() if lock else query)
    if item is None:
        fail("FILE_NOT_FOUND", "Arquivo não encontrado.", 404)
    _write_context(session, item, actor)
    return item


def _write_context(session, item, actor, *, creating=False):
    if item.purpose == "PRODUCT_IMAGE":
        from rotalog.catalog.service import image_product

        image_product(session, item.product_id, actor, write=True)
        return
    if actor.role != Role.DRIVER:
        fail(
            "CAPABILITY_REQUIRED",
            "Somente o motorista responsável envia arquivo operacional.",
            403,
        )
    from rotalog.logistics.delivery_service import authorized_route, evidence_context

    if item.purpose == "CARGO_ISSUE_PHOTO":
        route = authorized_route(session, item.route_id, actor)
        if route.state != "ACCEPTED":
            fail(
                "CARGO_READ_ONLY",
                "Fotos de conferência exigem carga ainda não recebida.",
            )
    else:
        attempt, _, _ = evidence_context(session, item.attempt_id, actor)
        if creating and attempt.state not in {"STARTED", "PROOF_PENDING"}:
            fail("ATTEMPT_INVALID_STATE", "Tentativa não aceita novos arquivos.")
    if not creating and item.uploaded_by != actor.id:
        fail("FILE_NOT_FOUND", "Arquivo não encontrado.", 404)


def _check_receivable(item):
    if item.status not in {"PENDING_UPLOAD", "QUARANTINED"} or item.confirmed_at:
        fail("FILE_NOT_RECEIVABLE", "Este arquivo não aceita novo conteúdo.")
    if item.expires_at <= datetime.now(UTC):
        fail("UPLOAD_INTENT_EXPIRED", "Intenção de upload expirada.")


def create_intent(data, key, actor, factory, settings):
    require_identity(settings)
    if data.size_bytes > settings.product_image_max_bytes:
        fail("FILE_SIZE_INVALID", "Tamanho acima do limite configurado.", 422)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN, Role.DRIVER}, write=True)
        _write_context(session, data, actor, creating=True)
        operation = "product-image:upload-intent"
        cached, digest = begin_idempotency(
            session,
            actor,
            operation,
            key,
            data.model_dump(mode="json", exclude_none=True),
        )
        if cached:
            return cached
        # Serializa a cota do ator sem manter lock durante upload ou processamento.
        quota_lock = int.from_bytes(
            hashlib.sha256(b"image-quota:" + actor.id.bytes).digest()[:8], signed=True
        )
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": quota_lock})
        pending = session.scalar(
            select(func.count())
            .select_from(StoredFile)
            .where(
                StoredFile.uploaded_by == actor.id,
                StoredFile.demo_run_id == actor.demo_run_id,
                StoredFile.status.in_(("PENDING_UPLOAD", "QUARANTINED", "SCANNING")),
                StoredFile.expires_at > datetime.now(UTC),
            )
        )
        if pending >= 20:
            fail(
                "UPLOAD_INTENT_LIMIT",
                "Limite de vinte uploads pendentes por usuário.",
                429,
            )
        recent = session.scalar(
            select(func.count())
            .select_from(StoredFile)
            .where(
                StoredFile.uploaded_by == actor.id,
                StoredFile.demo_run_id == actor.demo_run_id,
                StoredFile.created_at > datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        if recent >= 20:
            fail(
                "UPLOAD_RATE_LIMIT",
                "Limite de vinte intenções por minuto por usuário.",
                429,
            )
        item = StoredFile(
            **data.model_dump(exclude_none=True),
            uploaded_by=actor.id,
            organization_id=actor.organization_id,
            demo_run_id=actor.demo_run_id,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.product_image_upload_ttl_seconds),
        )
        session.add(item)
        session.flush()
        _event(session, item, "PENDING_UPLOAD", actor)
        response = _status(item) | {
            "upload_url": f"/api/v1/files/{item.id}/content",
            "method": "PUT",
            "headers": {"Content-Type": "application/octet-stream"},
            "requires_authentication": True,
            "max_bytes": settings.product_image_max_bytes,
            "max_pixels": settings.product_image_max_pixels,
        }
        return finish_idempotency(session, actor, operation, key, digest, response)


def upload_content(file_id, content, key, actor, factory, settings):
    require_identity(settings)
    if not content or len(content) > settings.product_image_max_bytes:
        fail("FILE_SIZE_INVALID", "Tamanho de imagem inválido.", 422)
    payload = {
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    operation = f"product-image:upload:{file_id}"
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN, Role.DRIVER}, write=True)
        item = _owned_file(session, file_id, actor)
        cached, _ = begin_idempotency(session, actor, operation, key, payload)
        if cached:
            return
        _check_receivable(item)
        run_id = item.demo_run_id
    # Escrita atômica no volume fora da transação. Se houver crash, reenvio recupera
    # o mesmo objeto imutável; nunca disponibiliza um arquivo sem commit posterior.
    PrivateImageStorage(settings).put(run_id, file_id, content)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN, Role.DRIVER}, write=True)
        item = _owned_file(session, file_id, actor, lock=True)
        cached, digest = begin_idempotency(session, actor, operation, key, payload)
        if cached:
            return
        _check_receivable(item)
        if item.uploaded_at and (
            item.received_sha256 != payload["sha256"]
            or item.received_size != len(content)
        ):
            fail(
                "FILE_CONTENT_IMMUTABLE", "Arquivo já recebido com conteúdo diferente."
            )
        item.status, item.received_size, item.received_sha256 = (
            "QUARANTINED",
            len(content),
            payload["sha256"],
        )
        item.uploaded_at = datetime.now(UTC)
        _event(session, item, "QUARANTINED", actor)
        finish_idempotency(
            session, actor, operation, key, digest, {"file_id": str(item.id)}
        )


def _inspect_stored(settings, run_id, file_id, size, sha, mime):
    try:
        content = PrivateImageStorage(settings).read(run_id, file_id)
    except (FileNotFoundError, OSError):
        return "FILE_CONTENT_MISSING", None
    except RotaLogError as error:
        return error.code, None
    if len(content) != size or hashlib.sha256(content).hexdigest() != sha:
        return "FILE_CHECKSUM_MISMATCH", None
    try:
        inspected = inspect_product_image(content, settings)
    except RotaLogError as error:
        return error.code, None
    if inspected[0] != mime:
        return "FILE_MIME_MISMATCH", None
    return None, inspected


def confirm_upload(file_id, key, actor, factory, settings):
    require_identity(settings)
    operation = f"product-image:confirm:{file_id}"
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN, Role.DRIVER}, write=True)
        item = _owned_file(session, file_id, actor)
        cached, _ = begin_idempotency(session, actor, operation, key, {})
        if cached:
            return cached
        _check_receivable(item)
        if not item.uploaded_at:
            fail("FILE_CONTENT_REQUIRED", "Envie o conteúdo antes de confirmar.")
        snapshot = (
            item.demo_run_id,
            item.id,
            item.size_bytes,
            item.sha256,
            item.mime_type,
        )
    rejection, _ = _inspect_stored(
        settings, *snapshot
    )  # Leitura/decodificação fora dos locks.
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN, Role.DRIVER}, write=True)
        item = _owned_file(session, file_id, actor, lock=True)
        cached, digest = begin_idempotency(session, actor, operation, key, {})
        if cached:
            return cached
        _check_receivable(item)
        if rejection:
            item.status, item.rejection_reason = "REJECTED", rejection
            _event(session, item, "REJECTED", actor)
            response = None
        else:
            item.confirmed_at = datetime.now(UTC)
            _event(session, item, "SCAN_QUEUED", actor)
            response = finish_idempotency(
                session, actor, operation, key, digest, _status(item)
            )
    if rejection:
        fail(rejection, "Arquivo rejeitado na confirmação; crie outra intenção.", 422)
    return response


def process_product_image_files(factory, settings, *, limit=20, file_id=None):
    """Fila PostgreSQL recuperável. Validação sintética NÃO comprova ausência de malware."""
    require_identity(settings)
    processed = 0
    for _ in range(limit):
        with transactional_session(factory) as session:
            now = datetime.now(UTC)
            item = session.scalar(
                select(StoredFile)
                .join(DemoRun)
                .where(
                    DemoRun.is_active.is_(True),
                    StoredFile.id == file_id if file_id else True,
                    or_(
                        and_(
                            StoredFile.status == "QUARANTINED",
                            StoredFile.confirmed_at.is_not(None),
                        ),
                        and_(
                            StoredFile.status == "SCANNING", StoredFile.scan_until < now
                        ),
                    ),
                )
                .order_by(StoredFile.created_at, StoredFile.id)
                .with_for_update(skip_locked=True, of=StoredFile)
                .limit(1)
            )
            if item is None:
                break
            item.status, item.scan_token = "SCANNING", uuid4()
            item.scan_until = now + timedelta(minutes=2)
            item.scanner = (
                "SYNTHETIC_IMAGE_VALIDATION_ONLY"
                if session.get(DemoRun, item.demo_run_id).is_synthetic
                else "IMAGE_VALIDATION_ONLY"
            )
            snapshot = (
                item.demo_run_id,
                item.id,
                item.size_bytes,
                item.sha256,
                item.mime_type,
            )
            lease = item.scan_token
            _event(session, item, "SCANNING")
        rejection, inspected = _inspect_stored(settings, *snapshot)
        with transactional_session(factory) as session:
            item = session.scalar(
                select(StoredFile).where(StoredFile.id == snapshot[1]).with_for_update()
            )
            if item.status != "SCANNING" or item.scan_token != lease:
                continue
            require_active_run(session, item.demo_run_id)
            item.status = "REJECTED" if rejection else "AVAILABLE"
            item.rejection_reason = rejection
            item.scan_until, item.scan_token = None, None
            if inspected:
                _, item.width, item.height = inspected
            _event(session, item, item.status)
            processed += 1
    return processed


def available_product_file(session, file_id, product_id, actor):
    """Serviço público para associação no catálogo; nenhum módulo altera StoredFile."""
    item = session.scalar(
        select(StoredFile).where(
            StoredFile.id == file_id,
            StoredFile.product_id == product_id,
            StoredFile.demo_run_id == actor.demo_run_id,
            StoredFile.organization_id == actor.organization_id,
            StoredFile.status == "AVAILABLE",
        )
    )
    if item is None:
        fail(
            "FILE_NOT_AVAILABLE",
            "Arquivo disponível não encontrado para este produto/contexto.",
        )
    return item


def _readable_file(session, file_id, actor):
    from rotalog.catalog.image_service import image_file_is_visible

    item = session.scalar(
        select(StoredFile).where(
            StoredFile.id == file_id, StoredFile.demo_run_id == actor.demo_run_id
        )
    )
    if item is None:
        fail("FILE_NOT_FOUND", "Arquivo não encontrado.", 404)
    if item.purpose != "PRODUCT_IMAGE":
        from rotalog.logistics.cargo_models import CargoIssuePhoto
        from rotalog.logistics.delivery_models import PrivateEvidence
        from rotalog.logistics.delivery_service import (
            authorized_route,
            evidence_context,
        )

        if item.purpose == "CARGO_ISSUE_PHOTO":
            authorized_route(session, item.route_id, actor)
            attached = session.scalar(
                select(CargoIssuePhoto.id).where(CargoIssuePhoto.file_id == item.id)
            )
            readable = (
                actor.id == item.uploaded_by or actor.role == Role.SUPPLIER and attached
            )
        else:
            if actor.role not in {Role.DRIVER, Role.BUYER, Role.SUPPLIER}:
                fail("FILE_NOT_FOUND", "Arquivo não encontrado.", 404)
            evidence_context(session, item.attempt_id, actor)
            attached = session.scalar(
                select(PrivateEvidence.id).where(PrivateEvidence.file_id == item.id)
            )
            readable = actor.id == item.uploaded_by or attached
        if not readable:
            fail(
                "FILE_NOT_FOUND",
                "Arquivo ainda não associado a recurso autorizado.",
                404,
            )
        return item
    if actor.role == Role.ADMIN and item.organization_id == actor.organization_id:
        return item
    if item.status != "AVAILABLE" or not image_file_is_visible(
        session, item.id, item.product_id, actor
    ):
        fail("FILE_NOT_FOUND", "Arquivo não encontrado.", 404)
    return item


def available_cargo_photo(session, file_id, route_id, actor):
    item = session.scalar(
        select(StoredFile).where(
            StoredFile.id == file_id,
            StoredFile.route_id == route_id,
            StoredFile.purpose == "CARGO_ISSUE_PHOTO",
            StoredFile.demo_run_id == actor.demo_run_id,
            StoredFile.uploaded_by == actor.id,
            StoredFile.status == "AVAILABLE",
        )
    )
    if item is None:
        fail("FILE_NOT_AVAILABLE", "Foto de conferência não disponível neste contexto.")
    from rotalog.logistics.cargo_models import CargoIssuePhoto

    if session.scalar(
        select(CargoIssuePhoto.id).where(CargoIssuePhoto.file_id == file_id)
    ):
        fail("FILE_ALREADY_ATTACHED", "Foto já associada a uma ocorrência.")
    return item


def available_attempt_file(session, file_id, attempt_id, kind, actor):
    item = session.scalar(
        select(StoredFile).where(
            StoredFile.id == file_id,
            StoredFile.attempt_id == attempt_id,
            StoredFile.purpose == f"DELIVERY_{kind}",
            StoredFile.demo_run_id == actor.demo_run_id,
            StoredFile.status == "AVAILABLE",
        )
    )
    if item is None:
        fail(
            "EVIDENCE_NOT_FOUND",
            "Arquivo disponível não encontrado para esta tentativa/tipo.",
            404,
        )
    return item


def get_status(file_id, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor)
        return _status(_readable_file(session, file_id, actor))


def download_access(file_id, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor)
        item = _readable_file(session, file_id, actor)
        if item.status != "AVAILABLE":
            fail("FILE_NOT_AVAILABLE", "Arquivo ainda não disponível.")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=60)
        session.add(
            FileDownloadGrant(
                file_id=item.id,
                user_id=actor.id,
                organization_id=actor.organization_id,
                session_id=actor.session_id,
                token_hash=token_hash(token),
                expires_at=expires,
            )
        )
        return {
            "file_id": str(item.id),
            "download_url": f"/api/v1/files/{item.id}/content",
            "method": "GET",
            "headers": {"X-Download-Token": token},
            "expires_at": expires,
            "requires_authentication": True,
        }


def read_content(file_id, actor, factory, settings, *, grant_token=None):
    with transactional_session(factory) as session:
        authorize(session, actor)
        item = _readable_file(session, file_id, actor)
        if item.status != "AVAILABLE":
            fail("FILE_NOT_AVAILABLE", "Arquivo ainda não disponível.")
        if grant_token is not None:
            grant = session.scalar(
                select(FileDownloadGrant)
                .where(
                    FileDownloadGrant.file_id == file_id,
                    FileDownloadGrant.token_hash == token_hash(grant_token),
                    FileDownloadGrant.user_id == actor.id,
                    FileDownloadGrant.organization_id == actor.organization_id,
                    FileDownloadGrant.session_id == actor.session_id,
                )
                .with_for_update()
            )
            if not grant or grant.used_at or grant.expires_at <= datetime.now(UTC):
                fail(
                    "DOWNLOAD_ACCESS_INVALID",
                    "Acesso expirado, usado ou inválido.",
                    403,
                )
            grant.used_at = datetime.now(UTC)
        snapshot = (
            item.demo_run_id,
            item.id,
            item.mime_type,
            item.sha256,
            item.size_bytes,
        )
    try:
        content = PrivateImageStorage(settings).read(snapshot[0], snapshot[1])
    except OSError:
        fail("FILE_CONTENT_MISSING", "Conteúdo temporariamente indisponível.", 404)
    if (
        hashlib.sha256(content).hexdigest() != snapshot[3]
        or len(content) != snapshot[4]
    ):
        fail(
            "FILE_INTEGRITY_FAILED",
            "Conteúdo indisponível por falha de integridade.",
            409,
        )
    return content, snapshot[2]
