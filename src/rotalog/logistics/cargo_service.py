"""Manifesto é projeção de FulfillmentLine; motorista declara, fornecedor corrige."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select

from rotalog.core.contracts import check_version, fail
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.models import Role
from rotalog.identity.service import authorize
from rotalog.logistics.cargo_models import (
    CargoEvent,
    CargoInspection,
    CargoIssue,
    CargoIssuePhoto,
)
from rotalog.logistics.delivery_models import RouteLoad, RoutePlanVersion
from rotalog.operations.service import begin_idempotency, finish_idempotency
from rotalog.ordering.models import Fulfillment, FulfillmentLine, OrderItem


def manifest_lines(session, route_id=None, *, order_id=None, fulfillment_ids=None):
    query = (
        select(FulfillmentLine, OrderItem, Fulfillment)
        .join(Fulfillment, Fulfillment.id == FulfillmentLine.fulfillment_id)
        .join(OrderItem, OrderItem.id == FulfillmentLine.order_item_id)
        .order_by(FulfillmentLine.id)
    )
    if route_id:
        query = query.join(RouteLoad, RouteLoad.fulfillment_id == Fulfillment.id).where(
            RouteLoad.route_id == route_id
        )
    else:
        query = query.where(Fulfillment.id.in_(fulfillment_ids or []))
    if order_id:
        query = query.where(Fulfillment.supplier_order_id == order_id)
    result = []
    for line, item, load in session.execute(query):
        snapshot = item.product_snapshot
        result.append(
            {
                "id": str(line.id),
                "fulfillment_line_id": str(line.id),
                "fulfillment_id": str(load.id),
                "order_item_id": str(item.id),
                "supplier_order_id": str(load.supplier_order_id),
                "product_id": snapshot["id"],
                "product_name": snapshot["name"],
                "brand": snapshot.get("brand"),
                "packaging_type": snapshot.get("packaging_type"),
                "packaging_quantity": snapshot.get("packaging_quantity"),
                "net_content": snapshot.get("net_content"),
                "net_content_unit": snapshot.get("net_content_unit"),
                "quantity_unit": "SELLABLE_PACKAGE",
                "expected_quantity": line.separated_quantity,
                "dispatched_quantity": line.dispatched_quantity,
            }
        )
    return result


def _fingerprint(lines):
    # Despachar não muda o manifesto conferido; mudar a carga física, sim.
    payload = [
        {k: v for k, v in item.items() if k != "dispatched_quantity"} for item in lines
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cargo_fingerprint(session, fulfillment_ids):
    return _fingerprint(manifest_lines(session, fulfillment_ids=fulfillment_ids))


def inspection(session, route_id):
    return session.scalar(
        select(CargoInspection).where(CargoInspection.route_id == route_id)
    )


def _issues(session, route_id):
    return list(
        session.scalars(
            select(CargoIssue)
            .where(CargoIssue.route_id == route_id)
            .order_by(CargoIssue.created_at, CargoIssue.id)
        )
    )


def _issue_response(session, item):
    return {
        "id": str(item.id),
        "fulfillment_line_id": str(item.fulfillment_line_id),
        "expected_quantity": item.expected_quantity,
        "observed_quantity": item.observed_quantity,
        "category": item.category,
        "reason": item.reason,
        "state": item.state,
        "correction": item.correction,
        "reported_by": str(item.reported_by),
        "resolved_by": str(item.resolved_by) if item.resolved_by else None,
        "created_at": item.created_at.isoformat(),
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "photo_ids": [
            str(i)
            for i in session.scalars(
                select(CargoIssuePhoto.file_id).where(
                    CargoIssuePhoto.issue_id == item.id
                )
            )
        ],
    }


def manifest_response(session, route):
    lines = manifest_lines(session, route.id)
    fingerprint = _fingerprint(lines)
    state = inspection(session, route.id)
    plan = session.get(RoutePlanVersion, route.plan_version_id)
    approved = plan.snapshot.get("cargo_fingerprint") or (
        state.fingerprint if state else None
    )
    changed = bool(approved and approved != fingerprint)
    issues = _issues(session, route.id)
    version = state.version + int(changed) if state else 1
    valid = bool(
        state and not changed and state.checked_version == version and state.checked_by
    )
    unresolved = sum(i.state == "OPEN" for i in issues)
    return {
        "route_id": str(route.id),
        "route_version": route.version,
        "manifest_version": version,
        "fingerprint": fingerprint,
        "inspection_required": state is not None,
        "requires_replanning": changed,
        "declaration_valid": valid and not unresolved,
        "checked_at": state.checked_at.isoformat()
        if state and state.checked_at
        else None,
        "received_at": state.received_at.isoformat()
        if state and state.received_at
        else None,
        "read_only": route.state != "ACCEPTED",
        "unresolved_issues": unresolved,
        "lines": lines,
        "total_sellable_packages": sum(i["expected_quantity"] for i in lines),
        "issues": [_issue_response(session, i) for i in issues],
        "is_synthetic": is_synthetic_data(),
    }


def get_manifest(route_id, actor, factory):
    from rotalog.logistics.delivery_service import authorized_route

    with transactional_session(factory) as session:
        authorize(session, actor, {Role.DRIVER, Role.SUPPLIER, Role.PLANNER})
        return manifest_response(session, authorized_route(session, route_id, actor))


def _state(session, route, lines):
    state = inspection(session, route.id)
    fingerprint = _fingerprint(lines)
    if state is None:
        state = CargoInspection(route_id=route.id, fingerprint=fingerprint)
        session.add(state)
        session.flush()
    elif state.fingerprint != fingerprint:
        fail(
            "CARGO_REPLAN_REQUIRED",
            "Carga diverge do plano aprovado; restaure a carga ou crie novo plano.",
        )
    return state


def validate_pickup(session, route, actor):
    """Legado mantém payload; rota inscrita no fluxo novo nunca contorna conferência."""
    view = manifest_response(session, route)
    if view["requires_replanning"]:
        fail(
            "CARGO_REPLAN_REQUIRED",
            "Carga mudou após aprovação; exige novo planejamento.",
        )
    state = inspection(session, route.id)
    if state is None:
        return
    if view["unresolved_issues"]:
        fail("CARGO_ISSUES_PENDING", "Resolva as divergências antes do recebimento.")
    if (
        not view["lines"]
        or not view["declaration_valid"]
        or state.checked_by != actor.id
    ):
        fail(
            "CARGO_RECHECK_REQUIRED",
            "Confira toda a carga e declare a versão atual do manifesto.",
        )


def record_receipt(session, route, actor):
    state = inspection(session, route.id)
    if state:
        state.received_by, state.received_at = actor.id, datetime.now(UTC)


def cargo_command(
    route_id, command, data, key, actor, factory, *, issue_id=None, request_id=None
):
    from rotalog.logistics.delivery_service import authorized_route, pickup_in_session

    role = Role.SUPPLIER if command == "resolve" else Role.DRIVER
    with transactional_session(factory) as session:
        authorize(session, actor, {role}, write=True)
        # Mesma ordem de locks usada nos comandos de rota: chave idempotente → rota.
        operation = f"cargo:{command}:{route_id}"
        payload = data.model_dump(mode="json") | {
            "issue_id": str(issue_id) if issue_id else None
        }
        cached, digest = begin_idempotency(session, actor, operation, key, payload)
        route = authorized_route(session, route_id, actor, lock=True)
        if cached:
            return cached
        check_version(route.version, data.expected_version)
        if route.state != "ACCEPTED":
            fail(
                "CARGO_READ_ONLY",
                "A conferência ocorre após aceite e antes do recebimento.",
            )
        lines = manifest_lines(session, route.id)
        if not lines:
            fail("CARGO_EMPTY", "Manifesto vazio não permite conferência/recebimento.")
        if manifest_response(session, route)["requires_replanning"]:
            fail(
                "CARGO_REPLAN_REQUIRED",
                "Carga mudou após aprovação; restaure-a ou obtenha nova aprovação.",
            )
        state = _state(session, route, lines)
        if hasattr(data, "manifest_version") and data.manifest_version != state.version:
            fail(
                "MANIFEST_VERSION_CONFLICT", "Manifesto mudou; confira a versão atual."
            )
        reason = "Início explícito da conferência pré-coleta"
        details = {}
        before = {
            "route_state": route.state,
            "route_version": route.version,
            "manifest_version": state.version,
            "checked_version": state.checked_version,
        }
        if command == "check":
            if any(i.state == "OPEN" for i in _issues(session, route.id)):
                fail(
                    "CARGO_ISSUES_PENDING",
                    "Resolva as divergências antes de declarar conferência.",
                )
            if set(map(str, data.checked_line_ids)) != {i["id"] for i in lines}:
                fail(
                    "CARGO_CHECK_INCOMPLETE",
                    "Confira exatamente todas as linhas do manifesto.",
                    422,
                )
            state.checked_version, state.checked_by, state.checked_at = (
                state.version,
                actor.id,
                datetime.now(UTC),
            )
            reason = "Motorista declarou conferência global da carga"
            details = {"checked_line_ids": [str(i) for i in data.checked_line_ids]}
        elif command == "report":
            line = next(
                (i for i in lines if i["id"] == str(data.fulfillment_line_id)), None
            )
            if line is None:
                fail("CARGO_LINE_NOT_FOUND", "Linha não pertence ao manifesto.", 404)
            if data.expected_quantity != line["expected_quantity"]:
                fail(
                    "CARGO_EXPECTED_QUANTITY_MISMATCH",
                    "Quantidade esperada deve corresponder ao manifesto.",
                )
            item = CargoIssue(
                route_id=route.id,
                reported_by=actor.id,
                **data.model_dump(
                    exclude={"expected_version", "manifest_version", "photo_ids"}
                ),
            )
            session.add(item)
            session.flush()
            from rotalog.operations.file_service import available_cargo_photo

            for file_id in data.photo_ids:
                available_cargo_photo(session, file_id, route.id, actor)
                session.add(CargoIssuePhoto(issue_id=item.id, file_id=file_id))
            state.version += 1
            state.checked_version = None
            reason, details = (
                data.reason,
                {
                    "issue_id": str(item.id),
                    "line_id": line["id"],
                    "expected_quantity": data.expected_quantity,
                    "observed_quantity": data.observed_quantity,
                },
            )
        elif command == "resolve":
            # _route já confirmou que o fornecedor participa; a origem única restringe à própria carga.
            item = session.scalar(
                select(CargoIssue)
                .where(CargoIssue.id == issue_id, CargoIssue.route_id == route.id)
                .with_for_update()
            )
            if item is None:
                fail(
                    "CARGO_ISSUE_NOT_FOUND",
                    "Divergência não encontrada nesta rota.",
                    404,
                )
            if item.state != "OPEN":
                fail("CARGO_ISSUE_ALREADY_RESOLVED", "Divergência já resolvida.")
            item.state, item.correction, item.resolved_by, item.resolved_at = (
                "RESOLVED",
                data.correction,
                actor.id,
                datetime.now(UTC),
            )
            state.version += 1
            state.checked_version = None
            reason, details = (
                data.correction,
                {
                    "issue_id": str(item.id),
                    "resolution": "RESTORED_TO_APPROVED_MANIFEST",
                },
            )
        elif command == "receipt":
            validate_pickup(session, route, actor)
            # Usa exatamente o mesmo caso de uso físico do endpoint de coleta existente.
            pickup_in_session(session, route, actor)
            reason = "Motorista confirmou recebimento da carga conferida"
        elif command != "begin":
            raise ValueError("Comando de carga desconhecido")
        route.version += 1
        details.update(
            origin="HTTP_API",
            before=before,
            after={
                "route_state": route.state,
                "route_version": route.version,
                "manifest_version": state.version,
                "checked_version": state.checked_version,
            },
            total_sellable_packages=sum(i["expected_quantity"] for i in lines),
        )
        session.add(
            CargoEvent(
                route_id=route.id,
                actor_id=actor.id,
                organization_id=actor.organization_id,
                role=actor.role,
                action=command.upper(),
                reason=reason,
                version=state.version,
                request_id=request_id,
                idempotency_key=key,
                details=details,
                is_synthetic=actor.data_is_synthetic,
            )
        )
        session.flush()
        response = manifest_response(session, route)
        return finish_idempotency(session, actor, operation, key, digest, response)
