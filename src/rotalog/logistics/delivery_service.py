"""Casos de uso transacionais da rota manual e entrega comprovada da demo."""

import hashlib
import hmac
import io
import math
import secrets
import time
import warnings
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from PIL import Image, UnidentifiedImageError
from sqlalchemy import exists, func, select

from rotalog.core.config import Settings
from rotalog.core.contracts import Page, check_version, fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.models import Role, User
from rotalog.identity.service import authorize, require_supplier_organization
from rotalog.inventory.models import InventoryProjection, Reservation, Warehouse
from rotalog.logistics.delivery_models import (
    CustodyEvent,
    DeliveryAttempt,
    DeliveryOutcomeLine,
    DeliveryProof,
    DeliveryProofLineEvidence,
    OrderEvent,
    PrivateEvidence,
    Route,
    RouteAssignment,
    RouteLoad,
    RoutePlanVersion,
    RouteStop,
    Vehicle,
)
from rotalog.logistics.delivery_schemas import (
    AttemptCreate,
    ManualPlanCreate,
    ProofInput,
    ReceiverAuthorization,
)
from rotalog.operations.service import begin_idempotency, finish_idempotency
from rotalog.ordering.models import (
    Fulfillment,
    FulfillmentLine,
    OrderItem,
    SupplierOrder,
)
from rotalog.organizations.models import Membership

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def _event(session, order_id, actor, kind, **details):
    session.add(
        OrderEvent(
            supplier_order_id=order_id, actor_id=actor.id, kind=kind, details=details
        )
    )


def _orders(session, route_id):
    return list(
        session.scalars(
            select(SupplierOrder)
            .join(Fulfillment)
            .join(RouteLoad, RouteLoad.fulfillment_id == Fulfillment.id)
            .where(RouteLoad.route_id == route_id)
            .distinct()
            .order_by(SupplierOrder.id)
        )
    )


def _route(session, route_id, actor, *, lock=False):
    route = session.scalar(
        select(Route)
        .where(Route.id == route_id, Route.demo_run_id == actor.demo_run_id)
        .with_for_update()
        if lock
        else select(Route).where(
            Route.id == route_id, Route.demo_run_id == actor.demo_run_id
        )
    )
    if route is None:
        fail("ROUTE_NOT_FOUND", "Rota não encontrada.", 404)
    assignment = session.scalar(
        select(RouteAssignment).where(RouteAssignment.route_id == route.id)
    )
    orders = _orders(session, route.id)
    permitted = (
        actor.role == Role.PLANNER
        and session.get(Warehouse, route.warehouse_id).supplier_organization_id
        == actor.organization_id
        or actor.role == Role.DRIVER
        and assignment.driver_id == actor.id
        or actor.role == Role.SUPPLIER
        and any(o.supplier_organization_id == actor.organization_id for o in orders)
        or actor.role == Role.BUYER
        and any(o.buyer_organization_id == actor.organization_id for o in orders)
    )
    if not permitted:
        fail("ROUTE_NOT_FOUND", "Rota não encontrada.", 404)
    return route


def authorized_route(session, route_id, actor, *, lock=False):
    """Fronteira pública para carga, tracking e arquivos; revalida vínculo da rota."""
    return _route(session, route_id, actor, lock=lock)


def route_summary(session, route):
    assignment = session.scalar(
        select(RouteAssignment).where(RouteAssignment.route_id == route.id)
    )
    return {
        "id": str(route.id),
        "state": route.state,
        "version": route.version,
        "driver_id": str(assignment.driver_id),
        "vehicle_id": str(assignment.vehicle_id),
        "updated_at": route.updated_at.isoformat(),
        "is_synthetic": is_synthetic_data(),
    }


def _route_response(session, route, actor):
    from rotalog.logistics.cargo_service import manifest_lines, manifest_response

    stops = []
    for stop in session.scalars(
        select(RouteStop)
        .where(RouteStop.route_id == route.id)
        .order_by(RouteStop.sequence)
    ):
        # Comprador só vê sua entrega e a origem, nunca endereço de outro comprador.
        if actor.role == Role.BUYER and stop.supplier_order_id:
            order = session.get(SupplierOrder, stop.supplier_order_id)
            if order.buyer_organization_id != actor.organization_id:
                continue
        attempt = session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.route_stop_id == stop.id)
        )
        stops.append(
            {
                "id": str(stop.id),
                "supplier_order_id": str(stop.supplier_order_id)
                if stop.supplier_order_id
                else None,
                "sequence": stop.sequence,
                "kind": stop.kind,
                "state": stop.state,
                "address_snapshot": stop.address_snapshot,
                "attempt_id": str(attempt.id) if attempt else None,
                "attempt_state": attempt.state if attempt else None,
                "attempt_version": attempt.version if attempt else None,
                "items": manifest_lines(
                    session, route.id, order_id=stop.supplier_order_id
                )
                if stop.kind == "DELIVERY"
                else [],
            }
        )
    privileged = actor.role in {Role.DRIVER, Role.SUPPLIER, Role.PLANNER}
    cargo = manifest_response(session, route) if privileged else None
    assignment = session.scalar(
        select(RouteAssignment).where(RouteAssignment.route_id == route.id)
    )
    vehicle = session.get(Vehicle, assignment.vehicle_id)
    actions = []
    if actor.role == Role.DRIVER:
        if route.state == "PUBLISHED":
            actions = ["accept"]
        elif route.state == "ACCEPTED":
            actions = ["inspect_cargo", "report_cargo_issue"]
            if cargo["declaration_valid"]:
                actions.append("receive_cargo")
            elif not cargo["inspection_required"]:
                actions.append("legacy_pickup")
        elif route.state == "READY_TO_START":
            actions = ["start"]
        elif route.state == "STARTED":
            actions = ["record_location", "execute_delivery", "end_execution"]
        elif route.state == "EXECUTION_ENDED" and all(
            i["state"] == "COMPLETED" for i in stops
        ):
            actions = ["complete_operation"]
    return route_summary(session, route) | {
        "warehouse_id": str(route.warehouse_id),
        "plan_version_id": str(route.plan_version_id),
        "stops": stops,
        "code": f"R-{str(route.id)[:8].upper()}",
        "scheduled_date": next(
            (
                datetime.fromisoformat(s["address_snapshot"]["starts_at"])
                .astimezone(ZoneInfo("America/Manaus"))
                .date()
                .isoformat()
                for s in stops
                if s["kind"] == "DELIVERY" and s["address_snapshot"].get("starts_at")
            ),
            None,
        ),
        "started_at": route.started_at.isoformat() if route.started_at else None,
        "execution_ended_at": route.execution_ended_at.isoformat()
        if route.execution_ended_at
        else None,
        "vehicle": {
            "id": str(vehicle.id),
            "registration": vehicle.registration,
            "package_units": vehicle.package_units,
            "weight_grams": vehicle.weight_grams,
            "volume_cm3": vehicle.volume_cm3,
        }
        if privileged
        else None,
        "cargo": {k: v for k, v in cargo.items() if k not in {"lines", "issues"}}
        if cargo
        else None,
        "allowed_actions": actions,
    }


def vehicles(data, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER}, write=data is not None)
        require_supplier_organization(session, actor)
        if data:
            if session.scalar(
                select(Vehicle.id).where(
                    Vehicle.demo_run_id == actor.demo_run_id,
                    Vehicle.registration == data.registration,
                )
            ):
                fail(
                    "VEHICLE_ALREADY_EXISTS", "Identificação de veículo já cadastrada."
                )
            item = Vehicle(
                **data.model_dump(),
                demo_run_id=actor.demo_run_id,
                supplier_organization_id=actor.organization_id,
            )
            session.add(item)
            session.flush()
            return dict(
                id=str(item.id),
                supplier_organization_id=str(actor.organization_id),
                **data.model_dump(),
                is_synthetic=is_synthetic_data(),
            )
        return [
            {
                "id": str(v.id),
                "registration": v.registration,
                "supplier_organization_id": str(v.supplier_organization_id),
                "package_units": v.package_units,
                "weight_grams": v.weight_grams,
                "volume_cm3": v.volume_cm3,
                "is_synthetic": is_synthetic_data(),
            }
            for v in session.scalars(
                select(Vehicle)
                .where(
                    Vehicle.demo_run_id == actor.demo_run_id,
                    Vehicle.supplier_organization_id == actor.organization_id,
                    Vehicle.is_active.is_(True),
                )
                .order_by(Vehicle.id)
                .limit(100)
            )
        ]


def drivers(actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER})
        require_supplier_organization(session, actor)
        return [
            {"id": str(u.id), "name": u.name, "is_synthetic": is_synthetic_data()}
            for u in session.scalars(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    User.demo_run_id == actor.demo_run_id,
                    Membership.role == Role.DRIVER,
                    Membership.organization_id == actor.organization_id,
                    Membership.is_active.is_(True),
                    User.is_active.is_(True),
                )
                .order_by(User.id)
                .limit(100)
            )
        ]


def eligible_fulfillments(actor, factory, page=1, page_size=25, warehouse_id=None):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER})
        require_supplier_organization(session, actor)
        valid_lines = (
            select(FulfillmentLine.id)
            .join(OrderItem)
            .where(
                FulfillmentLine.fulfillment_id == Fulfillment.id,
            )
        )
        invalid_lines = valid_lines.where(
            (FulfillmentLine.separated_quantity <= 0)
            | (FulfillmentLine.dispatched_quantity != 0)
            | (
                func.coalesce(
                    OrderItem.product_snapshot["gross_weight_grams"].as_integer(), 0
                )
                <= 0
            )
            | (
                func.coalesce(OrderItem.product_snapshot["volume_cm3"].as_integer(), 0)
                <= 0
            )
        )
        assigned = (
            select(RouteLoad.id)
            .join(Route)
            .where(
                RouteLoad.fulfillment_id == Fulfillment.id, Route.state != "CANCELLED"
            )
        )
        query = (
            select(Fulfillment)
            .join(SupplierOrder)
            .join(Warehouse, Warehouse.id == Fulfillment.warehouse_id)
            .where(
                SupplierOrder.demo_run_id == actor.demo_run_id,
                SupplierOrder.supplier_organization_id == actor.organization_id,
                SupplierOrder.state.in_(("READY_TO_DISPATCH", "PARTIALLY_DISPATCHED")),
                Warehouse.demo_run_id == actor.demo_run_id,
                Warehouse.supplier_organization_id == actor.organization_id,
                Warehouse.is_active.is_(True),
                Warehouse.geocoding_state == "CONFIRMED",
                Fulfillment.state == "READY_TO_DISPATCH",
                exists(valid_lines),
                ~exists(invalid_lines),
                ~exists(assigned),
            )
        )
        if warehouse_id:
            query = query.where(Fulfillment.warehouse_id == warehouse_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = []
        for load in session.scalars(
            query.order_by(Fulfillment.created_at, Fulfillment.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ):
            order = session.get(SupplierOrder, load.supplier_order_id)
            lines = session.execute(
                select(FulfillmentLine, OrderItem)
                .join(OrderItem)
                .where(FulfillmentLine.fulfillment_id == load.id)
                .order_by(FulfillmentLine.id)
            ).all()
            items.append(
                {
                    "id": load.id,
                    "supplier_order_id": order.id,
                    "supplier_organization_id": order.supplier_organization_id,
                    "buyer_organization_id": order.buyer_organization_id,
                    "warehouse_id": load.warehouse_id,
                    "state": load.state,
                    "version": load.version,
                    "package_units": sum(
                        line.separated_quantity for line, item in lines
                    ),
                    "weight_grams": sum(
                        line.separated_quantity
                        * item.product_snapshot["gross_weight_grams"]
                        for line, item in lines
                    ),
                    "volume_cm3": sum(
                        line.separated_quantity * item.product_snapshot["volume_cm3"]
                        for line, item in lines
                    ),
                    "lines": [
                        {
                            "id": line.id,
                            "order_item_id": item.id,
                            "separated_quantity": line.separated_quantity,
                            "product_snapshot": item.product_snapshot,
                        }
                        for line, item in lines
                    ],
                }
            )
        return Page(items=items, total=total, page=page, page_size=page_size)


def _validate_plan(session, snapshot, actor):
    from rotalog.logistics.cargo_service import cargo_fingerprint

    warehouse = session.scalar(
        select(Warehouse).where(
            Warehouse.id == UUID(snapshot["warehouse_id"]),
            Warehouse.demo_run_id == actor.demo_run_id,
            Warehouse.supplier_organization_id == actor.organization_id,
            Warehouse.is_active.is_(True),
        )
    )
    if not warehouse:
        fail("WAREHOUSE_NOT_FOUND", "Origem não encontrada.", 404)
    if warehouse.geocoding_state != "CONFIRMED":
        fail("WAREHOUSE_NOT_ELIGIBLE", "Origem precisa estar ativa e geocodificada.")
    vehicle = session.scalar(
        select(Vehicle)
        .where(
            Vehicle.id == UUID(snapshot["vehicle_id"]),
            Vehicle.demo_run_id == actor.demo_run_id,
            Vehicle.supplier_organization_id == actor.organization_id,
            Vehicle.is_active.is_(True),
        )
        .with_for_update()
    )
    driver = session.scalar(
        select(User)
        .where(
            User.id == UUID(snapshot["driver_id"]),
            User.demo_run_id == actor.demo_run_id,
            exists(
                select(Membership.id).where(
                    Membership.user_id == User.id,
                    Membership.organization_id == actor.organization_id,
                    Membership.role == Role.DRIVER,
                    Membership.is_active.is_(True),
                )
            ),
            User.is_active.is_(True),
        )
        .with_for_update()
    )
    if not vehicle or not driver:
        fail("ASSIGNMENT_NOT_ELIGIBLE", "Motorista ou veículo não elegível.", 404)
    loads = list(
        session.scalars(
            select(Fulfillment)
            .join(SupplierOrder)
            .where(
                Fulfillment.id.in_([UUID(i) for i in snapshot["fulfillment_ids"]]),
                SupplierOrder.demo_run_id == actor.demo_run_id,
                SupplierOrder.supplier_organization_id == actor.organization_id,
            )
            .order_by(Fulfillment.id)
            .with_for_update(of=Fulfillment)
        )
    )
    if len(loads) != len(snapshot["fulfillment_ids"]):
        fail("FULFILLMENT_NOT_ELIGIBLE", "Lote não encontrado.", 404)
    if snapshot.get("cargo_fingerprint") and snapshot[
        "cargo_fingerprint"
    ] != cargo_fingerprint(session, [load.id for load in loads]):
        fail(
            "PLAN_CARGO_CHANGED",
            "Carga mudou após proposta; crie uma nova versão para aprovação.",
        )
    order_ids = set()
    packages = weight = volume = 0
    for load in loads:
        order = session.get(SupplierOrder, load.supplier_order_id)
        if (
            order.demo_run_id != actor.demo_run_id
            or order.supplier_organization_id != actor.organization_id
            or load.warehouse_id != warehouse.id
            or load.state != "READY_TO_DISPATCH"
            or order.state not in {"READY_TO_DISPATCH", "PARTIALLY_DISPATCHED"}
        ):
            fail(
                "FULFILLMENT_NOT_ELIGIBLE", "Lote precisa estar pronto na mesma origem."
            )
        assigned = session.scalar(
            select(RouteLoad.id)
            .join(Route)
            .where(RouteLoad.fulfillment_id == load.id, Route.state != "CANCELLED")
        )
        if assigned:
            fail("FULFILLMENT_ALREADY_ASSIGNED", "Lote já alocado a outra rota.")
        order_ids.add(str(order.id))
        lines = list(
            session.scalars(
                select(FulfillmentLine).where(FulfillmentLine.fulfillment_id == load.id)
            )
        )
        if not lines:
            fail("EMPTY_FULFILLMENT", "Lote sem linhas.")
        for line in lines:
            product = session.get(OrderItem, line.order_item_id).product_snapshot
            if line.separated_quantity <= 0 or line.dispatched_quantity:
                fail("FULFILLMENT_NOT_ELIGIBLE", "Linha vazia ou já despachada.")
            if not product.get("gross_weight_grams") or not product.get("volume_cm3"):
                fail("LOGISTICS_DIMENSIONS_REQUIRED", "Peso e volume são obrigatórios.")
            packages += line.separated_quantity
            weight += line.separated_quantity * product["gross_weight_grams"]
            volume += line.separated_quantity * product["volume_cm3"]
    if (
        packages > vehicle.package_units
        or weight > vehicle.weight_grams
        or volume > vehicle.volume_cm3
    ):
        fail("VEHICLE_CAPACITY_EXCEEDED", "Carga excede a capacidade do veículo.")
    if {d["supplier_order_id"] for d in snapshot["deliveries"]} != order_ids:
        fail(
            "DELIVERY_STOPS_INVALID",
            "Informe exatamente as entregas dos pedidos alocados.",
            422,
        )
    previous_start = None
    for destination in snapshot["deliveries"]:
        start = datetime.fromisoformat(destination["starts_at"])
        end = datetime.fromisoformat(destination["ends_at"])
        if (
            end <= datetime.now(UTC)
            or (end - start).total_seconds() < destination["service_seconds"]
        ):
            fail("SERVICE_WINDOW_INVALID", "Janela expirada ou insuficiente.")
        if previous_start and start < previous_start:
            fail(
                "STOP_PRECEDENCE_INVALID",
                "Janelas incompatíveis com a sequência manual.",
            )
        previous_start = start
    return warehouse, loads


def _plan_response(plan):
    return {
        "id": str(plan.id),
        "state": plan.state,
        "version": plan.version,
        "snapshot": plan.snapshot,
        "reason": plan.reason,
        "supplier_organization_id": str(plan.supplier_organization_id)
        if plan.supplier_organization_id
        else None,
        "is_synthetic": is_synthetic_data(),
    }


def create_plan(data: ManualPlanCreate, key, actor, factory):
    from rotalog.logistics.cargo_service import cargo_fingerprint

    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER}, write=True)
        require_supplier_organization(session, actor)
        payload = data.model_dump(mode="json", exclude_none=True)
        operation = "manual-route-plan:create"
        cached, digest = begin_idempotency(session, actor, operation, key, payload)
        if cached:
            return cached
        _validate_plan(session, payload, actor)
        plan = RoutePlanVersion(
            demo_run_id=actor.demo_run_id,
            supplier_organization_id=actor.organization_id,
            created_by=actor.id,
            snapshot=payload
            | {"cargo_fingerprint": cargo_fingerprint(session, data.fulfillment_ids)},
            reason=data.reason,
        )
        session.add(plan)
        session.flush()
        for destination in data.deliveries:
            _event(
                session,
                destination.supplier_order_id,
                actor,
                "ROUTE_PLAN_PROPOSED",
                plan_id=str(plan.id),
            )
        return finish_idempotency(
            session, actor, operation, key, digest, _plan_response(plan)
        )


def plan_command(plan_id, command, data, key, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.PLANNER}, write=command != "get")
        require_supplier_organization(session, actor)
        plan = session.scalar(
            select(RoutePlanVersion)
            .where(
                RoutePlanVersion.id == plan_id,
                RoutePlanVersion.demo_run_id == actor.demo_run_id,
                RoutePlanVersion.supplier_organization_id == actor.organization_id,
            )
            .with_for_update()
        )
        if plan is None:
            fail("PLAN_NOT_FOUND", "Proposta não encontrada.", 404)
        if command == "get":
            return _plan_response(plan)
        operation = f"route-plan:{command}:{plan_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        if cached:
            return cached
        check_version(plan.version, data.expected_version)
        required = "PROPOSED" if command == "approve" else "APPROVED"
        if plan.state != required:
            fail("PLAN_INVALID_STATE", "Proposta não está no estado exigido.")
        warehouse, loads = _validate_plan(session, plan.snapshot, actor)
        if command == "approve":
            plan.approved_by, plan.state = actor.id, "APPROVED"
            response = None
        else:
            # Serializa conflitos de escala: um motorista/veículo não executa duas rotas.
            busy = session.scalar(
                select(Route.id)
                .join(RouteAssignment)
                .where(
                    Route.state.not_in(("CANCELLED", "OPERATIONALLY_COMPLETED")),
                    (RouteAssignment.driver_id == UUID(plan.snapshot["driver_id"]))
                    | (RouteAssignment.vehicle_id == UUID(plan.snapshot["vehicle_id"])),
                )
            )
            if busy:
                fail(
                    "ASSIGNMENT_BUSY", "Motorista ou veículo já está em uma rota ativa."
                )
            route = Route(
                plan_version_id=plan.id,
                warehouse_id=warehouse.id,
                demo_run_id=actor.demo_run_id,
            )
            session.add(route)
            session.flush()
            session.add(
                RouteAssignment(
                    route_id=route.id,
                    driver_id=UUID(plan.snapshot["driver_id"]),
                    vehicle_id=UUID(plan.snapshot["vehicle_id"]),
                    assigned_by=actor.id,
                )
            )
            for load in loads:
                session.add(RouteLoad(route_id=route.id, fulfillment_id=load.id))
            session.add(
                RouteStop(
                    route_id=route.id,
                    sequence=0,
                    kind="PICKUP",
                    address_snapshot={
                        "address": warehouse.address,
                        "latitude": str(warehouse.latitude),
                        "longitude": str(warehouse.longitude),
                    },
                )
            )
            for sequence, destination in enumerate(
                plan.snapshot["deliveries"], start=1
            ):
                session.add(
                    RouteStop(
                        route_id=route.id,
                        sequence=sequence,
                        kind="DELIVERY",
                        supplier_order_id=UUID(destination["supplier_order_id"]),
                        address_snapshot=destination,
                    )
                )
                _event(
                    session,
                    UUID(destination["supplier_order_id"]),
                    actor,
                    "ROUTE_PUBLISHED",
                    route_id=str(route.id),
                )
            plan.state = "PUBLISHED"
            session.flush()
            response = _route_response(session, route, actor)
        plan.version += 1
        session.flush()
        if response is None:
            response = _plan_response(plan)
        return finish_idempotency(session, actor, operation, key, digest, response)


def get_routes(
    actor,
    factory,
    page=1,
    page_size=25,
    route_id=None,
    *,
    state=None,
    active=None,
    settings=None,
):
    def response(session, route):
        result = _route_response(session, route, actor)
        if settings and actor.role in {Role.DRIVER, Role.PLANNER, Role.SUPPLIER}:
            from rotalog.logistics.tracking_service import current_view

            result["tracking"] = current_view(session, route, settings)
        return result

    with transactional_session(factory) as session:
        authorize(
            session, actor, {Role.PLANNER, Role.DRIVER, Role.BUYER, Role.SUPPLIER}
        )
        if route_id:
            return response(session, _route(session, route_id, actor))
        query = select(Route).where(Route.demo_run_id == actor.demo_run_id)
        if actor.role == Role.PLANNER:
            require_supplier_organization(session, actor)
            query = query.join(Warehouse).where(
                Warehouse.supplier_organization_id == actor.organization_id
            )
        if state:
            query = query.where(Route.state == state)
        if active is not None:
            final = ("CANCELLED", "OPERATIONALLY_COMPLETED")
            query = query.where(
                Route.state.not_in(final) if active else Route.state.in_(final)
            )
        if actor.role == Role.DRIVER:
            query = query.join(RouteAssignment).where(
                RouteAssignment.driver_id == actor.id
            )
        elif actor.role in {Role.BUYER, Role.SUPPLIER}:
            relevant = (
                select(RouteLoad.route_id)
                .join(Fulfillment)
                .join(SupplierOrder)
                .where(
                    SupplierOrder.buyer_organization_id == actor.organization_id
                    if actor.role == Role.BUYER
                    else SupplierOrder.supplier_organization_id == actor.organization_id
                )
            )
            query = query.where(Route.id.in_(relevant))
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return Page(
            items=[
                response(session, r)
                for r in session.scalars(
                    query.order_by(Route.created_at.desc(), Route.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ],
            page=page,
            page_size=page_size,
            total=total,
        )


def _load_lines(session, route_id, order_id=None):
    query = (
        select(FulfillmentLine)
        .join(Fulfillment)
        .join(RouteLoad, RouteLoad.fulfillment_id == Fulfillment.id)
        .where(RouteLoad.route_id == route_id)
    )
    if order_id:
        query = query.where(Fulfillment.supplier_order_id == order_id)
    return list(
        session.scalars(
            query.order_by(
                FulfillmentLine.order_item_id, FulfillmentLine.id
            ).with_for_update(of=FulfillmentLine)
        )
    )


def sync_order(session, order):
    """Estado comercial derivado; entrega não libera dinheiro."""
    session.flush()
    items = list(
        session.scalars(
            select(OrderItem).where(OrderItem.supplier_order_id == order.id)
        )
    )
    accepted = sum(i.supplier_accepted_quantity for i in items)
    dispatched = session.scalar(
        select(func.coalesce(func.sum(FulfillmentLine.dispatched_quantity), 0))
        .join(OrderItem)
        .where(OrderItem.supplier_order_id == order.id)
    )
    received = session.scalar(
        select(
            func.coalesce(
                func.sum(
                    DeliveryOutcomeLine.buyer_accepted_quantity
                    + DeliveryOutcomeLine.delivery_refused_quantity
                ),
                0,
            )
        )
        .join(FulfillmentLine)
        .join(OrderItem)
        .where(OrderItem.supplier_order_id == order.id)
    )
    if accepted and received == accepted:
        order.state = "DELIVERED"
    elif received:
        order.state = "PARTIALLY_DELIVERED"
    elif accepted and dispatched == accepted:
        order.state = "DISPATCHED"
    elif dispatched:
        order.state = "PARTIALLY_DISPATCHED"
    order.version += 1


def route_command(route_id, command, data, key, actor, factory):
    with transactional_session(factory) as session:
        authorize(
            session,
            actor,
            {Role.DRIVER} if command != "cancel" else {Role.PLANNER},
            write=True,
        )
        operation = f"route:{command}:{route_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        # Mesmo reenvio precisa manter autorização da rota.
        route = _route(session, route_id, actor, lock=True)
        if cached:
            return cached
        check_version(route.version, data.expected_version)
        transitions = {
            "accept": ("PUBLISHED", "ACCEPTED"),
            "pickup": ("ACCEPTED", "READY_TO_START"),
            "start": ("READY_TO_START", "STARTED"),
            "end-execution": ("STARTED", "EXECUTION_ENDED"),
            "complete-operation": ("EXECUTION_ENDED", "OPERATIONALLY_COMPLETED"),
        }
        if command == "cancel":
            if route.state not in {"PUBLISHED", "ACCEPTED"}:
                fail(
                    "ROUTE_INVALID_STATE", "Cancelar não pode abandonar carga coletada."
                )
            next_state = "CANCELLED"
        else:
            required, next_state = transitions[command]
            if route.state != required:
                fail("ROUTE_INVALID_STATE", "Transição de rota não permitida.")
        orders = _orders(session, route.id)
        for order in orders:
            session.refresh(order, with_for_update=True)
        if command == "pickup":
            pickup_in_session(session, route, actor)
        if command == "start":
            route.started_at = datetime.now(UTC)
        if command == "end-execution":
            route.execution_ended_at = datetime.now(UTC)
        if command == "complete-operation":
            unresolved = session.scalar(
                select(RouteStop.id).where(
                    RouteStop.route_id == route.id, RouteStop.state != "COMPLETED"
                )
            )
            if unresolved:
                fail("UNRESOLVED_STOPS", "Existem paradas/provas/custódia pendentes.")
        route.state, route.version = next_state, route.version + 1
        for order in orders:
            _event(
                session,
                order.id,
                actor,
                f"ROUTE_{command.upper().replace('-', '_')}",
                route_id=str(route.id),
                state=next_state,
            )
        session.flush()
        return finish_idempotency(
            session,
            actor,
            operation,
            key,
            digest,
            _route_response(session, route, actor),
        )


def pickup_in_session(session, route, actor):
    """Caso de uso físico compartilhado; transação é delimitada pelo chamador."""
    from rotalog.logistics.cargo_service import record_receipt, validate_pickup

    if route.state != "ACCEPTED":
        fail("ROUTE_INVALID_STATE", "Recebimento exige aceite e não inicia a rota.")
    validate_pickup(session, route, actor)
    orders = _orders(session, route.id)
    for order in orders:
        session.refresh(order, with_for_update=True)
    lines = _load_lines(session, route.id)
    if not lines:
        fail("CARGO_EMPTY", "Carga vazia não pode ser recebida.")
    for line in lines:
        qty = line.separated_quantity - line.dispatched_quantity
        if qty <= 0:
            fail("ALREADY_DISPATCHED", "Linha já despachada.")
        item = session.get(OrderItem, line.order_item_id)
        reservation = session.scalar(
            select(Reservation)
            .where(Reservation.id == item.reservation_id)
            .with_for_update()
        )
        projection = session.scalar(
            select(InventoryProjection)
            .where(InventoryProjection.id == reservation.projection_id)
            .with_for_update()
        )
        if (
            item.reserved_quantity < qty
            or projection.rotalog_reserved_not_reflected_quantity < qty
            or projection.external_sellable_quantity < qty
        ):
            fail("RESERVATION_INCONSISTENT", "Reserva insuficiente para a coleta.")
        line.dispatched_quantity += qty
        item.reserved_quantity -= qty
        reservation.consumed_quantity += qty
        projection.rotalog_reserved_not_reflected_quantity -= qty
        projection.external_sellable_quantity -= qty
        projection.version += 1
        session.add(
            CustodyEvent(
                route_id=route.id,
                fulfillment_line_id=line.id,
                actor_id=actor.id,
                kind="WAREHOUSE_TO_DRIVER",
                quantity=qty,
            )
        )
        session.get(Fulfillment, line.fulfillment_id).state = "DISPATCHED"
    pickup = session.scalar(
        select(RouteStop).where(
            RouteStop.route_id == route.id, RouteStop.kind == "PICKUP"
        )
    )
    pickup.state = "COMPLETED"
    route.state = "READY_TO_START"
    record_receipt(session, route, actor)
    for order in orders:
        sync_order(session, order)
        _event(session, order.id, actor, "CARGO_RECEIVED", route_id=str(route.id))


def _attempt_context(session, attempt_id, actor, *, lock=False):
    query = select(DeliveryAttempt).where(DeliveryAttempt.id == attempt_id)
    attempt = session.scalar(query.with_for_update() if lock else query)
    if not attempt:
        fail("ATTEMPT_NOT_FOUND", "Tentativa não encontrada.", 404)
    stop = session.get(RouteStop, attempt.route_stop_id)
    route = _route(session, stop.route_id, actor, lock=lock)
    if actor.role == Role.BUYER:
        order = session.get(SupplierOrder, stop.supplier_order_id)
        if order.buyer_organization_id != actor.organization_id:
            fail("ATTEMPT_NOT_FOUND", "Tentativa não encontrada.", 404)
    return attempt, stop, route


def evidence_context(session, attempt_id, actor):
    """Contexto público de tentativa para autorização no módulo de arquivos."""
    return _attempt_context(session, attempt_id, actor)


def _attempt_response(attempt):
    return {
        "id": str(attempt.id),
        "route_stop_id": str(attempt.route_stop_id),
        "state": attempt.state,
        "version": attempt.version,
        "is_synthetic": is_synthetic_data(),
    }


def create_attempt(stop_id, data: AttemptCreate, key, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        stop = session.get(RouteStop, stop_id)
        if not stop:
            fail("STOP_NOT_FOUND", "Parada não encontrada.", 404)
        route = _route(session, stop.route_id, actor, lock=True)
        operation = f"delivery-attempt:create:{stop_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        if cached:
            return cached
        check_version(route.version, data.expected_version)
        if (
            route.state != "STARTED"
            or stop.kind != "DELIVERY"
            or stop.state != "PENDING"
        ):
            fail("STOP_NOT_ELIGIBLE", "Parada não elegível para tentativa.")
        preceding = session.scalar(
            select(RouteStop.id).where(
                RouteStop.route_id == route.id,
                RouteStop.sequence < stop.sequence,
                RouteStop.state != "COMPLETED",
            )
        )
        if preceding:
            fail("PRECEDING_STOP_PENDING", "Conclua as paradas anteriores.")
        attempt = DeliveryAttempt(
            route_stop_id=stop.id, driver_id=actor.id, device_id=data.device_id
        )
        session.add(attempt)
        stop.state = "DELIVERY_IN_PROGRESS"
        route.version += 1
        session.flush()
        _event(
            session,
            stop.supplier_order_id,
            actor,
            "DELIVERY_ATTEMPT_STARTED",
            route_id=str(route.id),
            attempt_id=str(attempt.id),
        )
        return finish_idempotency(
            session, actor, operation, key, digest, _attempt_response(attempt)
        )


def _code_hash(code, attempt_id, settings):
    return hmac.new(
        settings.jwt_secret.get_secret_value().encode(),
        f"{attempt_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def authorize_receiver(
    attempt_id, data: ReceiverAuthorization, actor, factory, settings: Settings
):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.BUYER}, write=True)
        attempt, stop, _ = _attempt_context(session, attempt_id, actor, lock=True)
        if (
            attempt.state not in {"STARTED", "PROOF_PENDING"}
            or attempt.authorization_used_at
        ):
            fail("ATTEMPT_INVALID_STATE", "Tentativa não aceita nova autorização.")
        if (
            attempt.authorization_expires_at
            and attempt.authorization_expires_at > datetime.now(UTC)
        ):
            fail(
                "AUTHORIZATION_ALREADY_ISSUED",
                "Já existe um código válido; aguarde sua expiração para emitir outro.",
            )
        code = f"{secrets.randbelow(1000000):06d}"
        attempt.receiver_id, attempt.receiver_name, attempt.receiver_role = (
            actor.id,
            actor.name,
            data.receiver_role,
        )
        attempt.authorization_hash = _code_hash(code, attempt.id, settings)
        attempt.authorization_expires_at = datetime.now(UTC) + timedelta(minutes=10)
        attempt.authorization_failures = 0
        _event(
            session,
            stop.supplier_order_id,
            actor,
            "RECEIVER_AUTHORIZATION_ISSUED",
            attempt_id=str(attempt.id),
        )
        # Somente o comprador recebe o código; nunca armazenado em claro nem em timeline.
        return {
            "receiver_code": code,
            "expires_at": attempt.authorization_expires_at.isoformat(),
            "is_synthetic": is_synthetic_data(),
        }


def validate_image(content):
    if not content or len(content) > MAX_EVIDENCE_BYTES:
        fail("EVIDENCE_SIZE_INVALID", "Imagem deve ter no máximo 8 MiB.", 422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content), formats=["PNG", "JPEG"]) as image:
                if (
                    image.width * image.height > 16_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError("Imagem muito grande ou animada")
                fmt = image.format
                image.verify()
            with Image.open(io.BytesIO(content), formats=["PNG", "JPEG"]) as image:
                image.load()
        return "image/png" if fmt == "PNG" else "image/jpeg"
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        fail(
            "EVIDENCE_IMAGE_INVALID",
            "Envie PNG/JPEG válido, estático, até 16 megapixels.",
            422,
        )


def upload_evidence(attempt_id, kind, content, actor, factory, settings):
    """Compatibilidade multipart/201: aguarda validação sintética sem locks SQL.

    Novos clientes usam upload-intents/confirm-upload e aguardam o worker.
    O adaptador legado executa o mesmo processador para seu arquivo específico.
    """
    from rotalog.operations import file_service
    from rotalog.operations.file_schemas import UploadIntent

    mime = validate_image(content)  # CPU/decodificação fora da transação.
    digest = hashlib.sha256(content).hexdigest()
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        attempt, _, _ = _attempt_context(session, attempt_id, actor, lock=True)
        if attempt.state not in {"STARTED", "PROOF_PENDING"}:
            fail("ATTEMPT_INVALID_STATE", "Prova já finalizada.")
        existing = session.scalar(
            select(PrivateEvidence).where(
                PrivateEvidence.attempt_id == attempt.id,
                PrivateEvidence.kind == kind,
                PrivateEvidence.sha256 == digest,
            )
        )
        if existing:
            return _evidence_response(existing)
        count = session.scalar(
            select(func.count())
            .select_from(PrivateEvidence)
            .where(PrivateEvidence.attempt_id == attempt.id)
        )
        if count >= 10:
            fail("EVIDENCE_LIMIT", "Limite de arquivos da tentativa atingido.", 422)
    data = UploadIntent(
        purpose=f"DELIVERY_{kind}",
        attempt_id=attempt_id,
        size_bytes=len(content),
        mime_type=mime,
        sha256=digest,
    )
    key = f"legacy-evidence:{attempt_id}:{kind}:{digest}"
    intent = file_service.create_intent(data, key, actor, factory, settings)
    file_id = UUID(intent["file_id"])
    file_service.upload_content(file_id, content, key, actor, factory, settings)
    file_service.confirm_upload(file_id, key, actor, factory, settings)
    deadline = time.monotonic() + 10
    while True:
        file_service.process_product_image_files(
            factory, settings, limit=1, file_id=file_id
        )
        status = file_service.get_status(file_id, actor, factory)
        if status["status"] == "AVAILABLE":
            break
        if status["status"] == "REJECTED":
            fail(
                "EVIDENCE_REJECTED",
                "Arquivo rejeitado pelo processamento sintético.",
                422,
            )
        if time.monotonic() >= deadline:
            fail(
                "EVIDENCE_PROCESSING",
                "Arquivo ainda em processamento; reenvie o mesmo conteúdo para reconciliar.",
                503,
            )
        # Outro worker pode ter adquirido o lease. Espera curta, sempre fora do SQL.
        time.sleep(0.05)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        attempt, _, _ = _attempt_context(session, attempt_id, actor, lock=True)
        if attempt.state not in {"STARTED", "PROOF_PENDING"}:
            fail("ATTEMPT_INVALID_STATE", "Prova já finalizada.")
        return _evidence_response(
            _attach_available_file(session, file_id, attempt.id, kind, actor)
        )


def _attach_available_file(session, file_id, attempt_id, kind, actor):
    from rotalog.operations.file_service import available_attempt_file

    item = session.get(PrivateEvidence, file_id)
    if item is not None:
        if item.attempt_id != attempt_id or item.kind != kind:
            fail("EVIDENCE_NOT_FOUND", "Arquivo de outra tentativa/tipo.", 404)
        return item
    stored = available_attempt_file(session, file_id, attempt_id, kind, actor)
    count = session.scalar(
        select(func.count())
        .select_from(PrivateEvidence)
        .where(PrivateEvidence.attempt_id == attempt_id)
    )
    if count >= 10:
        fail("EVIDENCE_LIMIT", "Limite de arquivos da tentativa atingido.", 422)
    item = PrivateEvidence(
        id=stored.id,
        file_id=stored.id,
        attempt_id=attempt_id,
        uploaded_by=actor.id,
        kind=kind,
        mime_type=stored.mime_type,
        sha256=stored.sha256,
        content=b"",
    )
    session.add(item)
    session.flush()
    return item


def _evidence_response(item):
    return {
        "id": str(item.id),
        "kind": item.kind,
        "mime_type": item.mime_type,
        "sha256": item.sha256,
        "download_path": f"/api/v1/delivery-evidence/{item.id}",
    }


def evidence(evidence_id, actor, factory, settings):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER, Role.BUYER, Role.SUPPLIER})
        item = session.get(PrivateEvidence, evidence_id)
        if item is None:
            fail("EVIDENCE_NOT_FOUND", "Arquivo não encontrado.", 404)
        _attempt_context(session, item.attempt_id, actor)
        if item.file_id is None:
            return item.content, item.mime_type  # Preserva arquivos legados existentes.
        file_id = item.file_id
    from rotalog.operations.file_service import read_content

    return read_content(file_id, actor, factory, settings)


def _distance(a, b, c, d):
    """Haversine apenas para sinalização; geofence não decide entrega."""
    lat1, lat2 = math.radians(float(a)), math.radians(float(c))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(float(d) - float(b))
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return round(6371000 * 2 * math.asin(math.sqrt(min(1, value))))


def _complete_proof(session, attempt_id, data, key, actor, settings):
    attempt, stop, route = _attempt_context(session, attempt_id, actor, lock=True)
    operation = f"delivery-attempt:complete:{attempt_id}"
    payload = data.model_dump(mode="json", exclude={"receiver_code"})
    # Código não é persistido em registros de idempotência, somente seu HMAC.
    payload_digest = payload | {
        "authorization": _code_hash(data.receiver_code or "", attempt.id, settings)
    }
    cached, digest = begin_idempotency(session, actor, operation, key, payload_digest)
    if cached:
        return cached
    check_version(attempt.version, data.expected_version)
    if attempt.state not in {"STARTED", "PROOF_PENDING"} or route.state not in {
        "STARTED",
        "EXECUTION_ENDED",
    }:
        fail("ATTEMPT_INVALID_STATE", "Tentativa não pode ser concluída.")
    now = datetime.now(UTC)
    if data.captured_at < attempt.created_at - timedelta(
        minutes=5
    ) or data.captured_at > now + timedelta(minutes=5):
        fail(
            "PROOF_TIMESTAMP_INVALID",
            "Captura incompatível com o horário da tentativa.",
            422,
        )
    lines = _load_lines(session, route.id, stop.supplier_order_id)
    if {l.id for l in lines} != {l.fulfillment_line_id for l in data.lines}:
        fail(
            "DELIVERY_LINES_INVALID",
            "Informe todas as linhas da entrega, sem linhas de outro pedido.",
            422,
        )
    requested = {l.fulfillment_line_id: l for l in data.lines}
    for line in lines:
        outcome = requested[line.id]
        # Recorte integral: não encerrar casos excepcionais sem implementar retorno/reembolso.
        if (
            outcome.delivery_refused_quantity
            or outcome.buyer_accepted_quantity != line.dispatched_quantity
        ):
            fail(
                "DEMO_INTEGRAL_DELIVERY_ONLY",
                "Esta demo conclui apenas recebimento integral. Divergências permanecem pendentes de operação.",
            )
    files = []
    for evidence_id, kind in [(i, "PHOTO") for i in data.photo_ids] + (
        [(data.signature_id, "SIGNATURE")] if data.signature_id else []
    ):
        item = session.scalar(
            select(PrivateEvidence).where(
                PrivateEvidence.id == evidence_id,
                PrivateEvidence.attempt_id == attempt.id,
                PrivateEvidence.kind == kind,
            )
        )
        if not item:
            item = _attach_available_file(session, evidence_id, attempt.id, kind, actor)
        files.append(item)
    complete = bool(
        data.signature_id
        and data.photo_ids
        and data.receiver_code
        and attempt.authorization_hash
    )
    if complete:
        valid_code = (
            attempt.authorization_expires_at > now
            and not attempt.authorization_used_at
            and attempt.authorization_failures < 5
            and hmac.compare_digest(
                attempt.authorization_hash,
                _code_hash(data.receiver_code, attempt.id, settings),
            )
        )
        if not valid_code:
            attempt.authorization_failures += 1
            # O chamador lança o erro APÓS commit para preservar o limite de tentativas.
            return {"_authorization_error": True}
    proof = session.scalar(
        select(DeliveryProof).where(DeliveryProof.attempt_id == attempt.id)
    )
    if proof is None:
        proof = DeliveryProof(
            attempt_id=attempt.id, payload={}, validation_state="PROOF_PENDING"
        )
        session.add(proof)
    payload.update(
        route_id=str(route.id),
        route_stop_id=str(stop.id),
        driver_id=str(actor.id),
        device_id=attempt.device_id,
        received_at=now.isoformat(),
        receiver_id=str(attempt.receiver_id) if attempt.receiver_id else None,
        receiver_name=attempt.receiver_name,
        receiver_role=attempt.receiver_role,
        authorization_method="BUYER_ONE_TIME_CODE",
        distance_meters=_distance(
            data.latitude,
            data.longitude,
            stop.address_snapshot["latitude"],
            stop.address_snapshot["longitude"],
        ),
        evidence=[_evidence_response(f) for f in files],
    )
    payload["geofence_warning"] = payload["distance_meters"] > 150
    proof.payload = payload
    if not complete:
        attempt.state = "PROOF_PENDING"
        proof.validation_state = "PROOF_PENDING"
        _event(
            session,
            stop.supplier_order_id,
            actor,
            "PROOF_PENDING",
            attempt_id=str(attempt.id),
        )
    else:
        attempt.authorization_used_at = now
        attempt.state, proof.validation_state = "COMPLETED", "VALID"
        proof.validated_at, proof.contestation_deadline = now, now + timedelta(hours=24)
        for line in lines:
            result = requested[line.id]
            outcome = DeliveryOutcomeLine(
                attempt_id=attempt.id,
                fulfillment_line_id=line.id,
                buyer_accepted_quantity=result.buyer_accepted_quantity,
                delivery_refused_quantity=0,
                missing_quantity=0,
            )
            session.add(outcome)
            session.flush()
            for item in files:
                session.add(
                    DeliveryProofLineEvidence(
                        outcome_line_id=outcome.id, evidence_id=item.id
                    )
                )
            session.add(
                CustodyEvent(
                    route_id=route.id,
                    fulfillment_line_id=line.id,
                    actor_id=actor.id,
                    kind="DRIVER_TO_BUYER",
                    quantity=result.buyer_accepted_quantity,
                )
            )
        stop.state = "COMPLETED"
        order = session.scalar(
            select(SupplierOrder)
            .where(SupplierOrder.id == stop.supplier_order_id)
            .with_for_update()
        )
        sync_order(session, order)
        _event(
            session,
            stop.supplier_order_id,
            actor,
            "DELIVERY_PROOF_VALIDATED",
            attempt_id=str(attempt.id),
            route_id=str(route.id),
        )
    attempt.version += 1
    route.version += 1
    session.flush()
    return finish_idempotency(
        session,
        actor,
        operation,
        key,
        digest,
        _attempt_response(attempt)
        | {
            "proof_id": str(proof.id),
            "validation_state": proof.validation_state,
            "contestation_deadline": proof.contestation_deadline.isoformat()
            if proof.contestation_deadline
            else None,
        },
    )


def complete_attempt(attempt_id, data: ProofInput, key, actor, factory, settings):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        result = _complete_proof(session, attempt_id, data, key, actor, settings)
    if result.get("_authorization_error"):
        fail(
            "RECEIVER_AUTHORIZATION_INVALID",
            "Código inválido, expirado ou bloqueado.",
            403,
        )
    return result


def get_proof(attempt_id, actor, factory):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER, Role.BUYER, Role.SUPPLIER})
        attempt, _, _ = _attempt_context(session, attempt_id, actor)
        proof = session.scalar(
            select(DeliveryProof).where(DeliveryProof.attempt_id == attempt.id)
        )
        if proof is None:
            fail("PROOF_NOT_FOUND", "Ainda não há prova registrada.", 404)
        return {
            "id": str(proof.id),
            "attempt_id": str(attempt.id),
            "validation_state": proof.validation_state,
            "validated_at": proof.validated_at,
            "contestation_deadline": proof.contestation_deadline,
            "evidence": proof.payload,
            "is_synthetic": is_synthetic_data(),
        }


def timeline(order_id, actor, factory, page, page_size):
    from rotalog.ordering.service import _order

    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER, Role.BUYER})
        _order(session, order_id, actor)
        query = select(OrderEvent).where(OrderEvent.supplier_order_id == order_id)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return Page(
            items=[
                {
                    "id": str(e.id),
                    "kind": e.kind,
                    "occurred_at": e.created_at.isoformat(),
                    "details": e.details,
                }
                for e in session.scalars(
                    query.order_by(OrderEvent.created_at, OrderEvent.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
