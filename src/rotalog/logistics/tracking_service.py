"""Lotes idempotentes e projeção por sequência, sem incrementar Route.version."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from geoalchemy2 import WKTElement
from sqlalchemy import func, select, tuple_

from rotalog.core.contracts import fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.models import Role
from rotalog.identity.service import authorize
from rotalog.logistics.delivery_models import RouteStop
from rotalog.logistics.tracking_models import (
    CurrentRoutePosition,
    LocationBatch,
    RouteLocationPoint,
)


def _digest(data):
    return hashlib.sha256(
        json.dumps(
            data.model_dump(mode="json", exclude_none=True), sort_keys=True
        ).encode()
    ).hexdigest()


def _projection(session, route_id):
    return session.scalar(
        select(CurrentRoutePosition).where(CurrentRoutePosition.route_id == route_id)
    )


def _point_response(point):
    return {
        "id": str(point.id),
        "device_id": point.device_id,
        "sequence_no": point.sequence_no,
        "captured_at_device": point.captured_at_device.isoformat(),
        "received_at_server": point.created_at.isoformat(),
        "latitude": str(point.latitude),
        "longitude": str(point.longitude),
        "accuracy_meters": str(point.accuracy_meters),
        "flags": point.flags,
        "speed_mps": str(point.speed_mps) if point.speed_mps is not None else None,
        "bearing_degrees": str(point.bearing_degrees)
        if point.bearing_degrees is not None
        else None,
        "is_mocked_by_os": point.is_mocked_by_os,
    }


def current_view(session, route, settings):
    projection = _projection(session, route.id)
    point = (
        session.get(RouteLocationPoint, projection.point_id)
        if projection and projection.point_id
        else None
    )
    stale = point is None or datetime.now(UTC) - point.captured_at_device > timedelta(
        seconds=settings.tracking_stale_seconds
    )
    if route.state in {"EXECUTION_ENDED", "OPERATIONALLY_COMPLETED", "CANCELLED"}:
        status = "ENDED"
    elif route.state != "STARTED":
        status = "NOT_STARTED"
    elif projection and projection.declared_status in {
        "DEGRADED_OFFLINE",
        "DEGRADED_PERMISSION",
    }:
        status = projection.declared_status
    else:
        status = "STALE" if stale else "ACTIVE"
    return {
        "route_id": str(route.id),
        "state": status,
        "is_stale": stale,
        "position": _point_response(point) if point else None,
        "operational_version": route.version,
        "is_synthetic": is_synthetic_data(),
    }


def _authorized(session, route_id, actor, *, lock=False, history=False):
    from rotalog.logistics.delivery_service import authorized_route

    route = authorized_route(session, route_id, actor, lock=lock)
    if actor.role == Role.BUYER:
        if history or route.state != "STARTED":
            fail(
                "TRACKING_NOT_AVAILABLE",
                "Rastreamento não disponível neste contexto.",
                404,
            )
        from rotalog.ordering.models import SupplierOrder

        own_stop = session.scalar(
            select(RouteStop.id)
            .join(SupplierOrder, SupplierOrder.id == RouteStop.supplier_order_id)
            .where(
                RouteStop.route_id == route.id,
                RouteStop.kind == "DELIVERY",
                RouteStop.state != "COMPLETED",
                SupplierOrder.buyer_organization_id == actor.organization_id,
            )
        )
        if own_stop is None:
            fail(
                "TRACKING_NOT_AVAILABLE", "Entrega já recebida ou não autorizada.", 404
            )
    return route


def ingest(route_id, data, actor, factory, settings):
    from rotalog.logistics.delivery_service import _distance

    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        route = _authorized(session, route_id, actor, lock=True)
        if (
            data.route_id
            and data.route_id != route_id
            or data.driver_id
            and data.driver_id != actor.id
        ):
            fail(
                "TRACKING_CONTEXT_MISMATCH",
                "Rota/motorista não correspondem à sessão.",
                403,
            )
        digest = _digest(data)
        existing = session.scalar(
            select(LocationBatch).where(
                LocationBatch.route_id == route.id,
                LocationBatch.driver_id == actor.id,
                LocationBatch.client_batch_id == data.client_batch_id,
            )
        )
        if existing:
            if existing.payload_hash != digest:
                fail("IDEMPOTENCY_KEY_REUSED", "Lote já recebido com outro conteúdo.")
            return existing.response
        now = datetime.now(UTC)
        if (
            route.state not in {"STARTED", "EXECUTION_ENDED", "OPERATIONALLY_COMPLETED"}
            or not route.started_at
        ):
            fail(
                "TRACKING_ROUTE_INACTIVE",
                "Localização exige rota iniciada e atribuição vigente.",
            )
        if route.execution_ended_at and now > route.execution_ended_at + timedelta(
            seconds=settings.tracking_sync_window_seconds
        ):
            fail(
                "TRACKING_SYNC_WINDOW_CLOSED",
                "Janela técnica de sincronização encerrada.",
            )
        recent = session.scalar(
            select(func.count())
            .select_from(LocationBatch)
            .where(
                LocationBatch.route_id == route.id,
                LocationBatch.driver_id == actor.id,
                LocationBatch.created_at > now - timedelta(minutes=1),
            )
        )
        if recent >= settings.tracking_batches_per_minute:
            fail(
                "TRACKING_RATE_LIMIT",
                "Limite de lotes de rastreamento por minuto.",
                429,
            )
        batch = LocationBatch(
            route_id=route.id,
            driver_id=actor.id,
            session_id=actor.session_id,
            client_batch_id=data.client_batch_id,
            device_id=data.device_id,
            payload_hash=digest,
            response={},
        )
        session.add(batch)
        session.flush()
        projection = _projection(session, route.id)
        if projection is None:
            projection = CurrentRoutePosition(route_id=route.id)
            session.add(projection)
        current = (
            session.get(RouteLocationPoint, projection.point_id)
            if projection.point_id
            else None
        )
        results = []
        for p in sorted(data.points, key=lambda p: p.sequence_no):
            old = session.scalar(
                select(RouteLocationPoint).where(
                    RouteLocationPoint.route_id == route.id,
                    RouteLocationPoint.driver_id == actor.id,
                    RouteLocationPoint.device_id == data.device_id,
                    RouteLocationPoint.sequence_no == p.sequence_no,
                )
            )
            point_digest = _digest(p)
            if old:
                if old.payload_hash != point_digest:
                    fail(
                        "TRACKING_SEQUENCE_REUSED",
                        "Sequência já recebida com outros dados.",
                    )
                results.append(
                    {
                        "sequence_no": p.sequence_no,
                        "result": "DUPLICATE",
                        "point_id": str(old.id),
                        "flags": old.flags,
                    }
                )
                continue
            if (
                p.captured_at_device < route.started_at
                or route.execution_ended_at
                and p.captured_at_device > route.execution_ended_at
            ):
                results.append(
                    {
                        "sequence_no": p.sequence_no,
                        "result": "REJECTED",
                        "reason": "OUTSIDE_ACTIVE_PERIOD",
                    }
                )
                continue
            flags = []
            if p.accuracy_meters > settings.tracking_accuracy_warning_meters:
                flags.append("LOW_ACCURACY")
            if p.is_mocked_by_os:
                flags.append("MOCKED_BY_OS")
            if p.captured_at_device > now + timedelta(minutes=5):
                flags.append("CLOCK_AHEAD")
            if p.speed_mps is not None and p.speed_mps > 50:
                flags.append("SPEED_WARNING")
            previous = session.scalar(
                select(RouteLocationPoint)
                .where(
                    RouteLocationPoint.route_id == route.id,
                    RouteLocationPoint.device_id == data.device_id,
                    RouteLocationPoint.sequence_no < p.sequence_no,
                )
                .order_by(RouteLocationPoint.sequence_no.desc())
                .limit(1)
            )
            if previous:
                elapsed = (
                    p.captured_at_device - previous.captured_at_device
                ).total_seconds()
                if elapsed <= 0:
                    flags.append("CLOCK_INCONSISTENT")
                elif (
                    _distance(
                        previous.latitude, previous.longitude, p.latitude, p.longitude
                    )
                    / elapsed
                    > 50
                ):
                    flags.append("IMPLAUSIBLE_JUMP")
            point = RouteLocationPoint(
                route_id=route.id,
                driver_id=actor.id,
                batch_id=batch.id,
                device_id=data.device_id,
                sequence_no=p.sequence_no,
                captured_at_device=p.captured_at_device,
                latitude=p.latitude,
                longitude=p.longitude,
                accuracy_meters=p.accuracy_meters,
                speed_mps=p.speed_mps,
                bearing_degrees=p.bearing_degrees,
                is_mocked_by_os=p.is_mocked_by_os,
                position=WKTElement(f"POINT({p.longitude} {p.latitude})", srid=4326),
                flags=flags,
                payload_hash=point_digest,
            )
            session.add(point)
            session.flush()
            if not flags and (
                current is None
                or (point.sequence_no, point.captured_at_device)
                > (current.sequence_no, current.captured_at_device)
            ):
                projection.point_id, projection.declared_status, current = (
                    point.id,
                    None,
                    point,
                )
            results.append(
                {
                    "sequence_no": p.sequence_no,
                    "result": "STORED",
                    "point_id": str(point.id),
                    "flags": flags,
                }
            )
        batch.response = {
            "client_batch_id": str(data.client_batch_id),
            "route_id": str(route.id),
            "results": results,
            "operational_version": route.version,
            "is_synthetic": is_synthetic_data(),
        }
        session.flush()
        return batch.response


def get_current(route_id, actor, factory, settings):
    with transactional_session(factory) as session:
        authorize(
            session, actor, {Role.DRIVER, Role.PLANNER, Role.SUPPLIER, Role.BUYER}
        )
        return current_view(session, _authorized(session, route_id, actor), settings)


def get_history(route_id, actor, factory, *, cursor=None, page_size=50):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER, Role.PLANNER, Role.SUPPLIER})
        route = _authorized(session, route_id, actor, history=True)
        query = select(RouteLocationPoint).where(
            RouteLocationPoint.route_id == route.id
        )
        if cursor:
            try:
                seq, uid = json.loads(
                    base64.urlsafe_b64decode(cursor.encode()).decode()
                )
                if type(seq) is not int or seq < 0:
                    raise ValueError()
                uid = UUID(uid)
            except (ValueError, TypeError, UnicodeError):
                fail("TRACKING_CURSOR_INVALID", "Cursor de histórico inválido.", 422)
            query = query.where(
                tuple_(RouteLocationPoint.sequence_no, RouteLocationPoint.id)
                > (seq, uid)
            )
        points = list(
            session.scalars(
                query.order_by(
                    RouteLocationPoint.sequence_no, RouteLocationPoint.id
                ).limit(page_size + 1)
            )
        )
        more = len(points) > page_size
        points = points[:page_size]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps([points[-1].sequence_no, str(points[-1].id)]).encode()
            ).decode()
            if more
            else None
        )
        return {
            "route_id": str(route.id),
            "items": [_point_response(p) for p in points],
            "next_cursor": next_cursor,
            "is_synthetic": is_synthetic_data(),
        }


def change_status(route_id, data, actor, factory, settings):
    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER}, write=True)
        route = _authorized(session, route_id, actor, lock=True)
        if route.state != "STARTED":
            fail("TRACKING_ROUTE_INACTIVE", "Sinalização exige rota ativa.")
        projection = _projection(session, route.id)
        if projection is None:
            projection = CurrentRoutePosition(route_id=route.id)
            session.add(projection)
        projection.declared_status = data.status
        session.flush()
        return current_view(session, route, settings)
