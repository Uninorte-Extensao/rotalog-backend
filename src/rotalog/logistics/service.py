"""Casos de uso de rotas de entrega e localização conhecida."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from geoalchemy2 import WKTElement
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from rotalog.core.contracts import check_version, fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.models import Role, User
from rotalog.identity.schemas import UserResponse
from rotalog.identity.service import authorize
from rotalog.logistics.models import DeliveryRoute, DriverLocationPoint
from rotalog.logistics.schemas import DeliveryRouteCreate, LocationCreate
from rotalog.operations.service import begin_idempotency, finish_idempotency
from rotalog.ordering.models import SupplierOrder
from rotalog.organizations.models import Membership

LOCATION_STALE_AFTER = timedelta(minutes=2)
MAX_LOCATION_AGE = timedelta(days=1)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def _position(longitude: object, latitude: object) -> WKTElement:
    """Cria um ponto geográfico WGS 84 sem alterar os campos legados."""

    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _route_response(item: DeliveryRoute) -> dict:
    return {
        "id": str(item.id),
        "supplier_order_id": str(item.supplier_order_id),
        "driver_id": str(item.driver_id),
        "state": item.state,
        "version": item.version,
        "last_latitude": item.last_latitude,
        "last_longitude": item.last_longitude,
        "last_accuracy_meters": item.last_accuracy_meters,
        "last_location_at": item.last_location_at,
        "is_synthetic": is_synthetic_data(),
    }


def _get_route(
    session: Session, route_id: UUID, actor: UserResponse, *, lock: bool = False
) -> DeliveryRoute:
    query = (
        select(DeliveryRoute)
        .join(SupplierOrder)
        .where(
            DeliveryRoute.id == route_id,
            DeliveryRoute.demo_run_id == actor.demo_run_id,
        )
    )
    if actor.role == Role.DRIVER:
        query = query.where(DeliveryRoute.driver_id == actor.id)
    elif actor.role == Role.SUPPLIER:
        query = query.where(
            SupplierOrder.supplier_organization_id == actor.organization_id
        )
    elif actor.role == Role.BUYER:
        query = query.where(
            SupplierOrder.buyer_organization_id == actor.organization_id
        )
    elif actor.role != Role.ADMIN:
        fail("CAPABILITY_REQUIRED", "Permissão insuficiente para esta rota.", 403)
    if lock:
        query = query.with_for_update()
    route = session.scalar(query)
    if route is None:
        fail("DELIVERY_ROUTE_NOT_FOUND", "Rota de entrega não encontrada.", 404)
    return route


def create_route(
    order_id: UUID,
    data: DeliveryRouteCreate,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.SUPPLIER, Role.ADMIN}, write=True)
        order_query = select(SupplierOrder).where(
            SupplierOrder.id == order_id,
            SupplierOrder.demo_run_id == actor.demo_run_id,
        )
        if actor.role == Role.SUPPLIER:
            order_query = order_query.where(
                SupplierOrder.supplier_organization_id == actor.organization_id
            )
        order = session.scalar(order_query.with_for_update())
        if order is None:
            fail("SUPPLIER_ORDER_NOT_FOUND", "Pedido não encontrado.", 404)
        if order.state != "READY_TO_DISPATCH":
            fail(
                "ORDER_NOT_READY_TO_DISPATCH",
                "O pedido deve estar pronto para despacho antes de criar a rota.",
            )
        check_version(order.version, data.expected_version)
        existing = session.scalar(
            select(DeliveryRoute).where(DeliveryRoute.supplier_order_id == order.id)
        )
        if existing:
            fail("DELIVERY_ROUTE_ALREADY_EXISTS", "O pedido já possui uma rota.")
        driver = session.scalar(
            select(User).where(
                User.id == data.driver_id,
                User.demo_run_id == actor.demo_run_id,
                User.role == Role.DRIVER,
                User.is_active.is_(True),
                exists(
                    select(Membership.id).where(
                        Membership.user_id == User.id,
                        Membership.organization_id == order.supplier_organization_id,
                        Membership.role == Role.DRIVER,
                        Membership.is_active.is_(True),
                    )
                ),
            )
        )
        if driver is None:
            fail("DRIVER_NOT_FOUND", "Motorista ativo não encontrado.", 404)
        route = DeliveryRoute(
            supplier_order_id=order.id,
            driver_id=driver.id,
            demo_run_id=actor.demo_run_id,
        )
        session.add(route)
        session.flush()
        return _route_response(route)


def list_driver_routes(
    actor: UserResponse, factory: sessionmaker[Session]
) -> list[dict]:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER})
        return [
            _route_response(route)
            for route in session.scalars(
                select(DeliveryRoute)
                .where(
                    DeliveryRoute.driver_id == actor.id,
                    DeliveryRoute.demo_run_id == actor.demo_run_id,
                    DeliveryRoute.state.in_(("ASSIGNED", "IN_PROGRESS")),
                )
                .order_by(DeliveryRoute.created_at)
            )
        ]


def start_route(
    route_id: UUID,
    expected_version: int,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        route = _get_route(session, route_id, actor, lock=True)
        check_version(route.version, expected_version)
        if route.state != "ASSIGNED":
            fail("DELIVERY_ROUTE_INVALID_STATE", "A rota não pode ser iniciada.")
        route.state = "IN_PROGRESS"
        route.version += 1
        session.flush()
        return _route_response(route)


def complete_route(
    route_id: UUID,
    expected_version: int,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        route = _get_route(session, route_id, actor, lock=True)
        check_version(route.version, expected_version)
        if route.state != "IN_PROGRESS":
            fail("DELIVERY_ROUTE_INVALID_STATE", "A rota não pode ser concluída.")
        route.state = "COMPLETED"
        route.version += 1
        session.flush()
        return _route_response(route)


def record_location(
    route_id: UUID,
    data: LocationCreate,
    key: str,
    actor: UserResponse,
    factory: sessionmaker[Session],
) -> dict:
    now = datetime.now(UTC)
    if (
        data.recorded_at > now + MAX_CLOCK_SKEW
        or data.recorded_at < now - MAX_LOCATION_AGE
    ):
        fail("LOCATION_TIMESTAMP_INVALID", "Horário da localização não é aceito.", 422)
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        operation = f"delivery-route:location:{route_id}"
        cached, digest = begin_idempotency(
            session, actor, operation, key, data.model_dump(mode="json")
        )
        if cached:
            return cached
        route = _get_route(session, route_id, actor, lock=True)
        if route.state != "IN_PROGRESS":
            fail(
                "DELIVERY_ROUTE_INVALID_STATE",
                "A localização só pode ser enviada durante a rota em andamento.",
            )
        point = DriverLocationPoint(
            route_id=route.id,
            driver_id=actor.id,
            demo_run_id=actor.demo_run_id,
            latitude=data.latitude,
            longitude=data.longitude,
            position=_position(data.longitude, data.latitude),
            accuracy_meters=data.accuracy_meters,
            recorded_at=data.recorded_at,
        )
        session.add(point)
        if (
            route.last_location_at is None
            or point.recorded_at >= route.last_location_at
        ):
            route.last_latitude = point.latitude
            route.last_longitude = point.longitude
            route.last_position = _position(point.longitude, point.latitude)
            route.last_accuracy_meters = point.accuracy_meters
            route.last_location_at = point.recorded_at
        route.version += 1
        session.flush()
        response = {
            "id": str(point.id),
            "route_id": str(point.route_id),
            "driver_id": str(point.driver_id),
            "latitude": str(point.latitude),
            "longitude": str(point.longitude),
            "accuracy_meters": str(point.accuracy_meters),
            "recorded_at": point.recorded_at.isoformat(),
            "created_at": point.created_at.isoformat(),
            "is_synthetic": is_synthetic_data(),
        }
        return finish_idempotency(session, actor, operation, key, digest, response)


def last_location(
    route_id: UUID, actor: UserResponse, factory: sessionmaker[Session]
) -> dict:
    with transactional_session(factory) as session:
        authorize(session, actor)
        route = _get_route(session, route_id, actor)
        if route.last_location_at is None:
            fail("LOCATION_NOT_FOUND", "A rota ainda não possui localização.", 404)
        point = session.scalar(
            select(DriverLocationPoint)
            .where(DriverLocationPoint.route_id == route.id)
            .order_by(
                DriverLocationPoint.recorded_at.desc(), DriverLocationPoint.id.desc()
            )
            .limit(1)
        )
        if point is None:
            fail("LOCATION_NOT_FOUND", "A rota ainda não possui localização.", 404)
        return {
            "id": str(point.id),
            "route_id": str(point.route_id),
            "driver_id": str(point.driver_id),
            "latitude": point.latitude,
            "longitude": point.longitude,
            "accuracy_meters": point.accuracy_meters,
            "recorded_at": point.recorded_at,
            "is_stale": point.recorded_at < datetime.now(UTC) - LOCATION_STALE_AFTER,
            "is_synthetic": is_synthetic_data(),
        }
