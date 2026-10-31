"""Galeria canônica: alteração versionada e serializada por produto."""

from datetime import UTC, datetime

from sqlalchemy import select

from rotalog.catalog.image_schemas import ProductImageResponse
from rotalog.catalog.models import Product, ProductImage, ProductImageEvent
from rotalog.catalog.service import image_product
from rotalog.core.contracts import check_version, fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.models import Role
from rotalog.identity.security import require_identity
from rotalog.identity.service import authorize
from rotalog.operations.file_models import StoredFile
from rotalog.operations.file_service import available_product_file, read_content
from rotalog.operations.service import begin_idempotency, finish_idempotency


def _active_images(session, product_id):
    return list(
        session.scalars(
            select(ProductImage)
            .where(
                ProductImage.product_id == product_id, ProductImage.removed_at.is_(None)
            )
            .order_by(ProductImage.position, ProductImage.id)
        )
    )


def image_file_is_visible(session, file_id, product_id, actor):
    """Consulta pública usada pelo módulo de arquivos, sem alterar catálogo."""
    return bool(
        session.scalar(
            select(ProductImage.id)
            .join(Product)
            .where(
                ProductImage.file_id == file_id,
                ProductImage.product_id == product_id,
                ProductImage.removed_at.is_(None),
                Product.demo_run_id == actor.demo_run_id,
                Product.publication_state == "PUBLISHED",
            )
        )
    )


def gallery_metadata(session, product):
    rows = session.execute(
        select(ProductImage, StoredFile)
        .join(StoredFile)
        .where(
            ProductImage.product_id == product.id,
            ProductImage.removed_at.is_(None),
            StoredFile.product_id == product.id,
            StoredFile.demo_run_id == product.demo_run_id,
            StoredFile.status == "AVAILABLE",
        )
        .order_by(ProductImage.position, ProductImage.id)
    )
    images = [
        ProductImageResponse(
            id=image.id,
            file_id=item.id,
            position=image.position,
            is_primary=image.is_primary,
            alt_text=image.alt_text,
            mime_type=item.mime_type,
            size_bytes=item.size_bytes,
            sha256=item.sha256,
            width=item.width,
            height=item.height,
            content_url=f"/api/v1/products/{product.id}/images/{image.id}/content",
        )
        for image, item in rows
    ]
    return {
        "product_id": product.id,
        "product_version": product.version,
        "images": images,
        "primary_image_id": next(
            (image.id for image in images if image.is_primary), None
        ),
        "is_synthetic": is_synthetic_data(),
    }


def get_gallery(product_id, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor)
        return gallery_metadata(session, image_product(session, product_id, actor))


def gallery_command(
    product_id, command, data, key, actor, factory, settings, *, image_id=None
):
    require_identity(settings)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.ADMIN}, write=True)
        product = image_product(session, product_id, actor, write=True)
        operation = f"product-image:{command}:{product_id}"
        payload = data.model_dump(mode="json") | {
            "image_id": str(image_id) if image_id else None
        }
        cached, digest = begin_idempotency(session, actor, operation, key, payload)
        if cached:
            return cached
        check_version(product.version, data.expected_version)
        images = _active_images(session, product.id)
        target = next((image for image in images if image.id == image_id), None)
        if command == "attach":
            available_product_file(session, data.file_id, product.id, actor)
            if any(image.file_id == data.file_id for image in images):
                fail(
                    "PRODUCT_IMAGE_ALREADY_ATTACHED",
                    "Arquivo já associado a este produto.",
                )
            if len(images) >= settings.product_images_per_product:
                fail("PRODUCT_IMAGE_LIMIT", "Limite de imagens por produto atingido.")
            target = ProductImage(
                product_id=product.id,
                file_id=data.file_id,
                position=len(images),
                is_primary=not images,
                alt_text=data.alt_text or product.name,
            )
            session.add(target)
        elif command == "reorder":
            ids = [image.id for image in images]
            if len(data.image_ids) != len(set(data.image_ids)) or set(
                data.image_ids
            ) != set(ids):
                fail(
                    "PRODUCT_IMAGE_ORDER_INVALID",
                    "Informe exatamente todas as imagens ativas, sem repetição.",
                    422,
                )
            positions = {
                image_id: index for index, image_id in enumerate(data.image_ids)
            }
            for image in images:
                image.position = positions[image.id]
        elif command == "set-primary":
            if target is None:
                fail(
                    "PRODUCT_IMAGE_NOT_FOUND",
                    "Imagem não encontrada neste produto.",
                    404,
                )
            for image in images:
                image.is_primary = False
            session.flush()  # Libera índice único parcial antes de eleger outra principal.
            target.is_primary = True
        elif command == "remove":
            if target is None:
                fail(
                    "PRODUCT_IMAGE_NOT_FOUND",
                    "Imagem não encontrada neste produto.",
                    404,
                )
            was_primary = target.is_primary
            target.removed_at, target.is_primary = datetime.now(UTC), False
            session.flush()
            remaining = [image for image in images if image.id != target.id]
            for index, image in enumerate(remaining):
                image.position = index
                if was_primary:
                    image.is_primary = index == 0
        else:
            raise ValueError("Comando interno desconhecido")
        product.version += 1
        session.flush()
        session.add(
            ProductImageEvent(
                product_id=product.id,
                actor_id=actor.id,
                kind=f"IMAGE_{command.upper().replace('-', '_')}",
                version=product.version,
                details={
                    "image_id": str(target.id) if target else None,
                    "image_ids": [str(i) for i in data.image_ids]
                    if command == "reorder"
                    else [],
                },
            )
        )
        response = gallery_metadata(session, product)
        # JSON persistido não contém URLs assinadas, bytes, paths físicos ou credenciais.
        from rotalog.catalog.image_schemas import ProductGallery

        return finish_idempotency(
            session,
            actor,
            operation,
            key,
            digest,
            ProductGallery.model_validate(response).model_dump(mode="json"),
        )


def image_content(product_id, image_id, actor, factory, settings):
    with transactional_session(factory) as session:
        authorize(session, actor)
        image_product(session, product_id, actor)
        image = session.scalar(
            select(ProductImage).where(
                ProductImage.id == image_id,
                ProductImage.product_id == product_id,
                ProductImage.removed_at.is_(None),
            )
        )
        if image is None:
            fail("PRODUCT_IMAGE_NOT_FOUND", "Imagem não encontrada neste produto.", 404)
        file_id = image.file_id
    return read_content(file_id, actor, factory, settings)
