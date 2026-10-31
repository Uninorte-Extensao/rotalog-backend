"""Compra sintética de um fornecedor/depósito; reservas, quantidades e versões reais."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from rotalog.catalog.models import Offer, Product
from rotalog.catalog.schemas import ProductResponse
from rotalog.core.contracts import Page, check_version, fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.core.money import decimal_string
from rotalog.demo.models import DemoRun
from rotalog.demo.service import require_active_run
from rotalog.finance.models import PaymentAllocation
from rotalog.finance.service import capture_simulated, refund_refused
from rotalog.identity.models import Role
from rotalog.identity.schemas import UserResponse
from rotalog.identity.service import authorize
from rotalog.inventory.models import (
    CapacitySlot,
    InventoryProjection,
    Reservation,
    Warehouse,
)
from rotalog.inventory.service import convert_reservations, release, reserve
from rotalog.operations.service import begin_idempotency, finish_idempotency
from rotalog.ordering.models import (
    CheckoutLine,
    CheckoutSession,
    Fulfillment,
    FulfillmentLine,
    OrderItem,
    Purchase,
    SupplierOrder,
)
from rotalog.ordering.schemas import (
    Acceptance,
    Cancellation,
    CheckoutCreate,
    FulfillmentCreate,
    SeparationResults,
)


def _checkout_response(session: Session, item: CheckoutSession) -> dict:
    lines = session.scalars(
        select(CheckoutLine)
        .where(CheckoutLine.checkout_id == item.id)
        .order_by(CheckoutLine.id)
    ).all()
    purchase = session.scalar(select(Purchase).where(Purchase.checkout_id == item.id))
    return {
        "id": str(item.id),
        "state": item.state,
        "version": item.version,
        "supplier_organization_id": str(item.supplier_organization_id),
        "warehouse_id": str(item.warehouse_id),
        "capacity_slot_id": str(item.capacity_slot_id),
        "expires_at": item.expires_at.isoformat(),
        "accepts_partial": item.accepts_partial,
        "currency": "BRL",
        "total": decimal_string(
            sum(line.quantity * line.amount_minor for line in lines)
        ),
        "items": [
            {
                "offer_id": str(line.offer_id),
                "quantity": line.quantity,
                "unit_price": decimal_string(line.amount_minor),
                "currency": line.currency,
                "product": line.product_snapshot,
            }
            for line in lines
        ],
        "purchase_id": str(purchase.id) if purchase else None,
        "is_synthetic": is_synthetic_data(),
    }


def create_checkout(
    data: CheckoutCreate, key: str, actor: UserResponse, factory: sessionmaker[Session]
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER}, write=True)
        operation = "checkout:create"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        if cached:
            return cached
        offers = session.scalars(
            select(Offer)
            .where(
                Offer.id.in_([line.offer_id for line in data.items]),
                Offer.demo_run_id == actor.demo_run_id,
                Offer.is_active.is_(True),
            )
            .order_by(Offer.id)
            .with_for_update(read=True)
        ).all()
        if len(offers) != len(data.items):
            fail("OFFER_UNAVAILABLE", "Oferta não encontrada ou pausada.", 404)
        quantities = {item.offer_id: item.quantity for item in data.items}
        if (
            sum(offer.amount_minor * quantities[offer.id] for offer in offers)
            > 9_223_372_036_854_775_807
        ):
            fail(
                "TOTAL_AMOUNT_OUT_OF_RANGE",
                "Total excede o limite monetário do domínio.",
                422,
            )
        groups = {
            (offer.supplier_organization_id, offer.warehouse_id) for offer in offers
        }
        if len(groups) != 1 or offers[0].warehouse_id is None:
            fail(
                "DEMO_SINGLE_SUPPLIER",
                "A demo aceita um fornecedor e depósito por checkout.",
                422,
            )
        supplier_id, warehouse_id = next(iter(groups))
        warehouse = session.get(Warehouse, warehouse_id)
        if supplier_id == actor.organization_id or not warehouse.is_active:
            fail("OFFER_UNAVAILABLE", "Origem indisponível.")
        # Ordem global: checkout -> projeções ordenadas -> capacidade, também na expiração.
        session.scalars(
            select(InventoryProjection)
            .where(InventoryProjection.offer_id.in_([offer.id for offer in offers]))
            .order_by(InventoryProjection.id)
            .with_for_update()
        ).all()
        slot = session.scalar(
            select(CapacitySlot)
            .where(
                CapacitySlot.id == data.capacity_slot_id,
                CapacitySlot.warehouse_id == warehouse_id,
                CapacitySlot.ends_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        if slot is None:
            fail("CAPACITY_UNAVAILABLE", "Janela de capacidade indisponível.")
        checkout = CheckoutSession(
            buyer_organization_id=actor.organization_id,
            supplier_organization_id=supplier_id,
            warehouse_id=warehouse_id,
            capacity_slot_id=slot.id,
            demo_run_id=actor.demo_run_id,
            expires_at=min(datetime.now(UTC) + timedelta(minutes=15), slot.ends_at),
            accepts_partial=data.accepts_partial,
        )
        session.add(checkout)
        session.flush()
        for offer in offers:
            quantity = quantities[offer.id]
            product = session.get(Product, offer.product_id)
            if (
                product.publication_state != "PUBLISHED"
                or quantity < offer.minimum_order_quantity
            ):
                fail(
                    "OFFER_CONDITIONS_INVALID",
                    "Produto indisponível ou quantidade abaixo do mínimo.",
                    422,
                )
            reserve(
                session,
                checkout.id,
                offer.id,
                warehouse_id,
                slot,
                quantity,
                checkout.expires_at,
            )
            snapshot = ProductResponse.model_validate(product).model_dump(mode="json")
            session.add(
                CheckoutLine(
                    checkout_id=checkout.id,
                    offer_id=offer.id,
                    offer_version=offer.version,
                    product_snapshot=snapshot,
                    quantity=quantity,
                    amount_minor=offer.amount_minor,
                )
            )
        session.flush()
        return finish_idempotency(
            session,
            actor,
            operation,
            key,
            digest,
            _checkout_response(session, checkout),
        )


def get_checkout(
    checkout_id: UUID, actor: UserResponse, factory: sessionmaker[Session]
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER})
        checkout = session.scalar(
            select(CheckoutSession).where(
                CheckoutSession.id == checkout_id,
                CheckoutSession.buyer_organization_id == actor.organization_id,
                CheckoutSession.demo_run_id == actor.demo_run_id,
            )
        )
        if checkout is None:
            fail("CHECKOUT_NOT_FOUND", "Checkout não encontrado.", 404)
        return _checkout_response(session, checkout)


def _release_checkout(session: Session, checkout: CheckoutSession) -> None:
    reservations = session.scalars(
        select(Reservation)
        .where(Reservation.checkout_id == checkout.id, Reservation.state == "ACTIVE")
        .order_by(Reservation.projection_id)
    ).all()
    for reservation in reservations:
        release(session, reservation, reservation.quantity, expired=True)
    checkout.state = "EXPIRED"
    checkout.version += 1


def confirm_payment(
    checkout_id: UUID,
    expected_version: int,
    key: str,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    expired = False
    result = None
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER}, write=True)
        operation = f"checkout:payment:{checkout_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, {"expected_version": expected_version}
        )
        if cached:
            return cached
        checkout = session.scalar(
            select(CheckoutSession)
            .where(
                CheckoutSession.id == checkout_id,
                CheckoutSession.buyer_organization_id == actor.organization_id,
                CheckoutSession.demo_run_id == actor.demo_run_id,
            )
            .with_for_update()
        )
        if checkout is None:
            fail("CHECKOUT_NOT_FOUND", "Checkout não encontrado.", 404)
        check_version(checkout.version, expected_version)
        if checkout.state != "READY_FOR_PAYMENT":
            fail(
                "CHECKOUT_INVALID_STATE", "Checkout não está disponível para pagamento."
            )
        if checkout.expires_at <= datetime.now(UTC):
            _release_checkout(session, checkout)
            expired = True
        else:
            reservations = convert_reservations(session, checkout.id)
            purchase = Purchase(
                checkout_id=checkout.id,
                buyer_organization_id=actor.organization_id,
                demo_run_id=actor.demo_run_id,
            )
            session.add(purchase)
            session.flush()
            supplier_order = SupplierOrder(
                purchase_id=purchase.id,
                buyer_organization_id=actor.organization_id,
                supplier_organization_id=checkout.supplier_organization_id,
                warehouse_id=checkout.warehouse_id,
                demo_run_id=actor.demo_run_id,
                state="AWAITING_PAYMENT",
                accepts_partial=checkout.accepts_partial,
                acceptance_deadline=datetime.now(UTC) + timedelta(hours=24),
            )
            session.add(supplier_order)
            session.flush()
            items = []
            for line in session.scalars(
                select(CheckoutLine).where(CheckoutLine.checkout_id == checkout.id)
            ):
                item = OrderItem(
                    supplier_order_id=supplier_order.id,
                    offer_id=line.offer_id,
                    reservation_id=reservations[line.offer_id].id,
                    product_snapshot=line.product_snapshot,
                    offer_version=line.offer_version,
                    amount_minor=line.amount_minor,
                    requested_quantity=line.quantity,
                    reserved_quantity=line.quantity,
                )
                session.add(item)
                items.append(item)
            session.flush()
            payment = capture_simulated(session, purchase.id, items)
            supplier_order.state = "AWAITING_SUPPLIER_DECISION"
            checkout.state = "CONVERTED"
            checkout.version += 1
            result = {
                "purchase_id": str(purchase.id),
                "supplier_order_ids": [str(supplier_order.id)],
                "payment_id": str(payment.id),
                "payment_state": "CAPTURED",
                "provider": "SIMULATED",
                "total": decimal_string(payment.amount_minor),
                "currency": "BRL",
                "is_synthetic": True,
            }
            finish_idempotency(session, actor, operation, key, digest, result)
    if expired:
        fail("CHECKOUT_EXPIRED", "Checkout expirado; reservas liberadas.")
    return result


def _order(
    session: Session,
    order_id: UUID,
    actor: UserResponse,
    *,
    lock: bool = False,
    supplier_only: bool = False,
) -> SupplierOrder:
    if actor.role == Role.SUPPLIER:
        ownership = SupplierOrder.supplier_organization_id == actor.organization_id
    elif actor.role == Role.BUYER and not supplier_only:
        ownership = SupplierOrder.buyer_organization_id == actor.organization_id
    else:
        fail("CAPABILITY_REQUIRED", "Permissão insuficiente.", 403)
    query = select(SupplierOrder).where(
        SupplierOrder.id == order_id,
        ownership,
        SupplierOrder.demo_run_id == actor.demo_run_id,
        SupplierOrder.state != "AWAITING_PAYMENT",
    )
    if lock:
        query = query.with_for_update()
    item = session.scalar(query)
    if item is None:
        fail("SUPPLIER_ORDER_NOT_FOUND", "Pedido não encontrado.", 404)
    return item


def _fulfillment_response(session: Session, item: Fulfillment) -> dict:
    return {
        "id": str(item.id),
        "supplier_order_id": str(item.supplier_order_id),
        "warehouse_id": str(item.warehouse_id),
        "state": item.state,
        "version": item.version,
        "is_synthetic": is_synthetic_data(),
        "lines": [
            {
                "id": str(line.id),
                "order_item_id": str(line.order_item_id),
                "allocated_quantity": line.allocated_quantity,
                "separated_quantity": line.separated_quantity,
                "dispatched_quantity": line.dispatched_quantity,
            }
            for line in session.scalars(
                select(FulfillmentLine)
                .where(FulfillmentLine.fulfillment_id == item.id)
                .order_by(FulfillmentLine.id)
            )
        ],
    }


def _order_response(session: Session, item: SupplierOrder, actor: UserResponse) -> dict:
    from rotalog.logistics.delivery_models import DeliveryOutcomeLine, Route, RouteLoad
    from rotalog.logistics.delivery_service import route_summary

    lines = []
    total_minor = 0
    for line in session.scalars(
        select(OrderItem)
        .where(OrderItem.supplier_order_id == item.id)
        .order_by(OrderItem.id)
    ):
        total_minor += line.requested_quantity * line.amount_minor
        separated = session.scalar(
            select(
                func.coalesce(func.sum(FulfillmentLine.separated_quantity), 0)
            ).where(FulfillmentLine.order_item_id == line.id)
        )
        allocation = session.scalar(
            select(PaymentAllocation).where(PaymentAllocation.order_item_id == line.id)
        )
        dispatched = session.scalar(
            select(
                func.coalesce(func.sum(FulfillmentLine.dispatched_quantity), 0)
            ).where(FulfillmentLine.order_item_id == line.id)
        )
        outcomes = session.execute(
            select(
                func.coalesce(func.sum(DeliveryOutcomeLine.buyer_accepted_quantity), 0),
                func.coalesce(
                    func.sum(DeliveryOutcomeLine.delivery_refused_quantity), 0
                ),
                func.coalesce(func.sum(DeliveryOutcomeLine.missing_quantity), 0),
            )
            .join(FulfillmentLine)
            .where(FulfillmentLine.order_item_id == line.id)
        ).one()
        lines.append(
            {
                "id": str(line.id),
                "offer_id": str(line.offer_id),
                "product": line.product_snapshot,
                "unit_price": decimal_string(line.amount_minor),
                "currency": line.currency,
                "offer_version": line.offer_version,
                "requested_quantity": line.requested_quantity,
                "reserved_quantity": line.reserved_quantity,
                "supplier_accepted_quantity": line.supplier_accepted_quantity,
                "supplier_refused_quantity": line.supplier_refused_quantity,
                "separated_quantity": separated,
                "dispatched_quantity": dispatched,
                "delivered_quantity": outcomes[0] + outcomes[1],
                "buyer_accepted_quantity": outcomes[0],
                "delivery_refused_quantity": outcomes[1],
                "missing_quantity": outcomes[2],
                "returning_quantity": 0,
                "return_reconciled_quantity": 0,
                "refunded_quantity": allocation.refunded_quantity if allocation else 0,
                "financially_released_quantity": 0,
                "financially_pending_quantity": outcomes[0]
                if allocation and allocation.state == "PROTECTED"
                else 0,
            }
        )
    allowed = []
    if actor.role == Role.SUPPLIER:
        if item.state == "AWAITING_SUPPLIER_DECISION":
            allowed.append("acceptance")
        if item.state in {"ACCEPTED", "PARTIALLY_ACCEPTED"}:
            allowed.append("start-separation")
        if item.state == "SEPARATING":
            allowed.append("fulfillments")
    if (
        actor.role == Role.SUPPLIER
        and item.state
        in {"AWAITING_SUPPLIER_DECISION", "ACCEPTED", "PARTIALLY_ACCEPTED"}
    ) or (actor.role == Role.BUYER and item.state == "AWAITING_SUPPLIER_DECISION"):
        allowed.append("cancel")
    return {
        "id": str(item.id),
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "delivery_routes": [
            route_summary(session, route)
            for route in session.scalars(
                select(Route)
                .join(RouteLoad)
                .join(Fulfillment, Fulfillment.id == RouteLoad.fulfillment_id)
                .where(Fulfillment.supplier_order_id == item.id)
                .distinct()
                .order_by(Route.created_at, Route.id)
            )
        ],
        "purchase_id": str(item.purchase_id),
        "buyer_organization_id": str(item.buyer_organization_id),
        "supplier_organization_id": str(item.supplier_organization_id),
        "warehouse_id": str(item.warehouse_id),
        "state": item.state,
        "version": item.version,
        "accepts_partial": item.accepts_partial,
        "acceptance_deadline": item.acceptance_deadline.isoformat(),
        "currency": item.currency,
        "total": decimal_string(total_minor),
        "cancellation_reason": item.cancellation_reason,
        "items": lines,
        "allowed_actions": allowed,
        "is_synthetic": is_synthetic_data(),
        "fulfillments": [
            _fulfillment_response(session, row)
            for row in session.scalars(
                select(Fulfillment)
                .where(Fulfillment.supplier_order_id == item.id)
                .order_by(Fulfillment.id)
            )
        ],
    }


def get_order(
    order_id: UUID, actor: UserResponse, factory: sessionmaker[Session]
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor)
        return _order_response(session, _order(session, order_id, actor), actor)


def list_orders(
    actor: UserResponse, factory: sessionmaker[Session], page: int, page_size: int
) -> Page[dict]:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER, Role.SUPPLIER})
        ownership = (
            SupplierOrder.supplier_organization_id == actor.organization_id
            if actor.role == Role.SUPPLIER
            else SupplierOrder.buyer_organization_id == actor.organization_id
        )
        query = select(SupplierOrder).where(
            ownership,
            SupplierOrder.demo_run_id == actor.demo_run_id,
            SupplierOrder.state != "AWAITING_PAYMENT",
        )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return Page(
            items=[
                _order_response(session, item, actor)
                for item in session.scalars(
                    query.order_by(SupplierOrder.created_at.desc(), SupplierOrder.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ],
            total=total,
            page=page,
            page_size=page_size,
        )


def _purchase_response(
    session: Session, purchase: Purchase, actor: UserResponse
) -> dict:
    orders = session.scalars(
        select(SupplierOrder)
        .where(SupplierOrder.purchase_id == purchase.id)
        .order_by(SupplierOrder.id)
    ).all()
    return {
        "id": str(purchase.id),
        "checkout_id": str(purchase.checkout_id),
        "state": "CANCELLED"
        if all(
            item.state in {"REFUSED", "CANCELLED", "ACCEPTANCE_EXPIRED"}
            for item in orders
        )
        else "ACTIVE",
        "supplier_orders": [_order_response(session, item, actor) for item in orders],
        "is_synthetic": is_synthetic_data(),
    }


def purchases(
    actor: UserResponse,
    factory: sessionmaker[Session],
    page: int,
    page_size: int,
    purchase_id: UUID | None = None,
):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER})
        query = select(Purchase).where(
            Purchase.buyer_organization_id == actor.organization_id,
            Purchase.demo_run_id == actor.demo_run_id,
        )
        if purchase_id:
            item = session.scalar(query.where(Purchase.id == purchase_id))
            if item is None:
                fail("PURCHASE_NOT_FOUND", "Compra não encontrada.", 404)
            return _purchase_response(session, item, actor)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return Page(
            items=[
                _purchase_response(session, item, actor)
                for item in session.scalars(
                    query.order_by(Purchase.created_at.desc(), Purchase.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ],
            total=total,
            page=page,
            page_size=page_size,
        )


def acceptance(
    order_id: UUID,
    data: Acceptance,
    key: str,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER}, write=True)
        operation = f"acceptance:{order_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        if cached:
            return cached
        order = _order(session, order_id, actor, lock=True, supplier_only=True)
        check_version(order.version, data.expected_version)
        if (
            order.state != "AWAITING_SUPPLIER_DECISION"
            or order.acceptance_deadline <= datetime.now(UTC)
        ):
            fail(
                "SUPPLIER_ORDER_INVALID_TRANSITION",
                "Pedido fora do prazo ou já decidido.",
            )
        items = session.scalars(
            select(OrderItem).where(OrderItem.supplier_order_id == order.id)
        ).all()
        accepted = {line.order_item_id: line.accepted_quantity for line in data.lines}
        if len(accepted) != len(data.lines) or set(accepted) != {
            item.id for item in items
        }:
            fail(
                "ACCEPTANCE_LINES_INVALID",
                "Informe todas as linhas uma única vez.",
                422,
            )
        if any(accepted[item.id] > item.reserved_quantity for item in items):
            fail(
                "ACCEPTANCE_QUANTITY_INVALID",
                "Quantidade aceita excede a reservada.",
                422,
            )
        if not order.accepts_partial and not (
            all(accepted[item.id] == item.requested_quantity for item in items)
            or all(value == 0 for value in accepted.values())
        ):
            fail("PARTIAL_NOT_ALLOWED", "Comprador não autorizou aceite parcial.", 422)
        # Trava todas as projeções em ordem antes de reduzir reserva/capacidade.
        reservations = {
            item.id: session.get(Reservation, item.reservation_id) for item in items
        }
        projection_ids = [row.projection_id for row in reservations.values()]
        session.scalars(
            select(InventoryProjection)
            .where(InventoryProjection.id.in_(projection_ids))
            .order_by(InventoryProjection.id)
            .with_for_update()
        ).all()
        for item in items:
            item.supplier_accepted_quantity = accepted[item.id]
            item.supplier_refused_quantity = item.requested_quantity - accepted[item.id]
            item.reserved_quantity = accepted[item.id]
            if item.supplier_refused_quantity:
                release(session, reservations[item.id], item.supplier_refused_quantity)
                refund_refused(
                    session,
                    item.id,
                    item.supplier_refused_quantity * item.amount_minor,
                    item.supplier_refused_quantity,
                )
        order.state = (
            "REFUSED"
            if not any(accepted.values())
            else (
                "PARTIALLY_ACCEPTED"
                if any(item.supplier_refused_quantity for item in items)
                else "ACCEPTED"
            )
        )
        order.version += 1
        session.flush()
        return finish_idempotency(
            session,
            actor,
            operation,
            key,
            digest,
            _order_response(session, order, actor),
        )


def start_separation(
    order_id: UUID,
    expected_version: int,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER}, write=True)
        order = _order(session, order_id, actor, lock=True, supplier_only=True)
        check_version(order.version, expected_version)
        if order.state not in {"ACCEPTED", "PARTIALLY_ACCEPTED"}:
            fail(
                "SUPPLIER_ORDER_INVALID_TRANSITION",
                "Pedido não pode iniciar separação.",
            )
        order.state = "SEPARATING"
        order.version += 1
        return _order_response(session, order, actor)


def create_fulfillment(
    order_id: UUID,
    data: FulfillmentCreate,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER}, write=True)
        order = _order(session, order_id, actor, lock=True, supplier_only=True)
        check_version(order.version, data.expected_version)
        if order.state != "SEPARATING":
            fail("SUPPLIER_ORDER_INVALID_TRANSITION", "Pedido não está em separação.")
        items = {
            item.id: item
            for item in session.scalars(
                select(OrderItem).where(OrderItem.supplier_order_id == order.id)
            )
        }
        if len({line.order_item_id for line in data.lines}) != len(data.lines):
            fail("FULFILLMENT_LINES_INVALID", "Linhas duplicadas.", 422)
        for line in data.lines:
            item = items.get(line.order_item_id)
            allocated = session.scalar(
                select(
                    func.coalesce(func.sum(FulfillmentLine.allocated_quantity), 0)
                ).where(FulfillmentLine.order_item_id == line.order_item_id)
            )
            if (
                item is None
                or allocated + line.allocated_quantity > item.supplier_accepted_quantity
            ):
                fail(
                    "FULFILLMENT_QUANTITY_INVALID",
                    "Alocação excede a quantidade aceita.",
                    422,
                )
        fulfillment = Fulfillment(
            supplier_order_id=order.id, warehouse_id=order.warehouse_id
        )
        session.add(fulfillment)
        session.flush()
        for line in data.lines:
            session.add(
                FulfillmentLine(fulfillment_id=fulfillment.id, **line.model_dump())
            )
        order.version += 1
        session.flush()
        return _fulfillment_response(session, fulfillment)


def separation(
    fulfillment_id: UUID,
    data: SeparationResults | None,
    expected_version: int,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER}, write=True)
        candidate = session.scalar(
            select(Fulfillment)
            .join(SupplierOrder)
            .where(
                Fulfillment.id == fulfillment_id,
                SupplierOrder.supplier_organization_id == actor.organization_id,
                SupplierOrder.demo_run_id == actor.demo_run_id,
            )
        )
        if candidate is None:
            fail("FULFILLMENT_NOT_FOUND", "Lote não encontrado.", 404)
        order = _order(
            session, candidate.supplier_order_id, actor, lock=True, supplier_only=True
        )
        fulfillment = session.scalar(
            select(Fulfillment)
            .where(Fulfillment.id == fulfillment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        check_version(fulfillment.version, expected_version)
        if order.state != "SEPARATING" or fulfillment.state != "SEPARATING":
            fail("FULFILLMENT_INVALID_STATE", "Lote não está em separação.")
        lines = session.scalars(
            select(FulfillmentLine).where(
                FulfillmentLine.fulfillment_id == fulfillment.id
            )
        ).all()
        if data:
            values = {
                line.fulfillment_line_id: line.separated_quantity for line in data.lines
            }
            if len(values) != len(data.lines) or set(values) != {
                line.id for line in lines
            }:
                fail(
                    "SEPARATION_LINES_INVALID",
                    "Informe todas as linhas uma única vez.",
                    422,
                )
            for line in lines:
                if values[line.id] > line.allocated_quantity:
                    fail(
                        "SEPARATION_QUANTITY_INVALID",
                        "Quantidade excede a alocação.",
                        422,
                    )
                line.separated_quantity = values[line.id]
        else:
            if any(
                line.separated_quantity != line.allocated_quantity for line in lines
            ):
                fail(
                    "FULFILLMENT_NOT_SEPARATED",
                    "Separe todas as quantidades antes de declarar pronto.",
                )
            fulfillment.state = "READY_TO_DISPATCH"
        fulfillment.version += 1
        order.version += 1
        session.flush()
        items = session.scalars(
            select(OrderItem).where(OrderItem.supplier_order_id == order.id)
        ).all()
        all_ready = not session.scalar(select(exists_unready(order.id)))
        if all_ready and all(
            session.scalar(
                select(
                    func.coalesce(func.sum(FulfillmentLine.separated_quantity), 0)
                ).where(FulfillmentLine.order_item_id == item.id)
            )
            == item.supplier_accepted_quantity
            for item in items
        ):
            order.state = "READY_TO_DISPATCH"
        return _fulfillment_response(session, fulfillment)


def exists_unready(order_id: UUID):
    from sqlalchemy import exists

    return exists().where(
        Fulfillment.supplier_order_id == order_id,
        Fulfillment.state != "READY_TO_DISPATCH",
    )


def expire_checkouts(factory: sessionmaker[Session]) -> int:
    """Worker local: expiração usa os mesmos locks da confirmação de pagamento."""
    count = 0
    with transactional_session(factory) as session:
        checkouts = session.scalars(
            select(CheckoutSession)
            .join(DemoRun, DemoRun.id == CheckoutSession.demo_run_id)
            .where(
                CheckoutSession.state == "READY_FOR_PAYMENT",
                CheckoutSession.expires_at <= datetime.now(UTC),
                DemoRun.is_active.is_(True),
            )
            .order_by(CheckoutSession.id)
            .limit(100)
            .with_for_update(skip_locked=True, of=CheckoutSession)
        ).all()
        for checkout in checkouts:
            _release_checkout(session, checkout)
            count += 1
    return count


def _cancel(session: Session, order: SupplierOrder, reason: str, state: str) -> None:
    items = session.scalars(
        select(OrderItem).where(OrderItem.supplier_order_id == order.id)
    ).all()
    reservations = {
        item.id: session.get(Reservation, item.reservation_id) for item in items
    }
    session.scalars(
        select(InventoryProjection)
        .where(
            InventoryProjection.id.in_(
                [row.projection_id for row in reservations.values()]
            )
        )
        .order_by(InventoryProjection.id)
        .with_for_update()
    ).all()
    for item in items:
        release(session, reservations[item.id], item.reserved_quantity)
        item.reserved_quantity = 0
        # Cancelamento não reescreve a decisão histórica do fornecedor.
        refund_refused(
            session,
            item.id,
            item.requested_quantity * item.amount_minor,
            item.requested_quantity,
        )
    order.state, order.cancellation_reason = state, reason
    order.version += 1


def cancel_order(
    order_id: UUID,
    data: Cancellation,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER, Role.SUPPLIER}, write=True)
        order = _order(session, order_id, actor, lock=True)
        check_version(order.version, data.expected_version)
        allowed = (
            {"AWAITING_SUPPLIER_DECISION"}
            if actor.role == Role.BUYER
            else {"AWAITING_SUPPLIER_DECISION", "ACCEPTED", "PARTIALLY_ACCEPTED"}
        )
        if order.state not in allowed:
            fail(
                "CANCELLATION_NOT_ELIGIBLE",
                "Cancelamento direto indisponível neste estado.",
            )
        _cancel(session, order, data.reason, "CANCELLED")
        session.flush()
        return _order_response(session, order, actor)


def expire_orders(factory: sessionmaker[Session]) -> int:
    count = 0
    with transactional_session(factory) as session:
        candidates = session.scalars(
            select(SupplierOrder.id)
            .join(DemoRun, DemoRun.id == SupplierOrder.demo_run_id)
            .where(
                SupplierOrder.state == "AWAITING_SUPPLIER_DECISION",
                SupplierOrder.acceptance_deadline <= datetime.now(UTC),
                DemoRun.is_active.is_(True),
            )
            .order_by(SupplierOrder.id)
            .limit(100)
        ).all()
    for order_id in candidates:
        with transactional_session(factory) as session:
            order = session.scalar(
                select(SupplierOrder)
                .where(
                    SupplierOrder.id == order_id,
                    SupplierOrder.state == "AWAITING_SUPPLIER_DECISION",
                    SupplierOrder.acceptance_deadline <= datetime.now(UTC),
                )
                .with_for_update(skip_locked=True)
            )
            if order is not None:
                require_active_run(session, order.demo_run_id)
                _cancel(
                    session,
                    order,
                    "Prazo sintético de aceite expirado",
                    "ACCEPTANCE_EXPIRED",
                )
                count += 1
    return count
