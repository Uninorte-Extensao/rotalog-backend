"""Adaptadores HTTP finos para planejamento manual e entrega comprovada."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Header, Query, Response, UploadFile

from rotalog.core.contracts import Page, Versioned
from rotalog.identity import service as identity_service
from rotalog.identity.dependencies import (
    ApplicationSettings,
    CurrentUser,
    SessionFactory,
)
from rotalog.identity.schemas import DriverProvisionRequest, UserResponse
from rotalog.logistics import delivery_service as service
from rotalog.logistics.delivery_schemas import (
    AttemptCreate,
    AttemptResponse,
    EligibleFulfillmentResponse,
    EvidenceResponse,
    ManualPlanCreate,
    PlanResponse,
    ProofInput,
    ReceiverAuthorization,
    RouteResponse,
    VehicleCreate,
)

router = APIRouter(tags=["rotas e entrega comprovada"])
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get(
    "/route-planning/eligible-fulfillments",
    response_model=Page[EligibleFulfillmentResponse],
)
def eligible_fulfillments(
    actor: CurrentUser,
    factory: SessionFactory,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    warehouse_id: UUID | None = None,
):
    return service.eligible_fulfillments(actor, factory, page, page_size, warehouse_id)


@router.post("/vehicles", status_code=201)
def create_vehicle(data: VehicleCreate, actor: CurrentUser, factory: SessionFactory):
    return service.vehicles(data, actor, factory)


@router.get("/vehicles")
def vehicles(actor: CurrentUser, factory: SessionFactory):
    return service.vehicles(None, actor, factory)


@router.get("/drivers")
def drivers(actor: CurrentUser, factory: SessionFactory):
    return service.drivers(actor, factory)


@router.post("/drivers", response_model=UserResponse, status_code=201)
def create_driver(
    data: DriverProvisionRequest,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
):
    return identity_service.provision_driver(data, actor, factory, settings)


@router.post(
    "/route-plan-versions",
    status_code=201,
    response_model=PlanResponse,
    summary="Criar proposta manual da demo (extensão do contrato V5)",
)
def manual_plan(
    data: ManualPlanCreate, key: Key, actor: CurrentUser, factory: SessionFactory
):
    return service.create_plan(data, key, actor, factory)


@router.get("/route-plan-versions/{plan_id}", response_model=PlanResponse)
def plan(plan_id: UUID, actor: CurrentUser, factory: SessionFactory):
    return service.plan_command(plan_id, "get", None, None, actor, factory)


@router.post("/route-plan-versions/{plan_id}/approve", response_model=PlanResponse)
def approve(
    plan_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.plan_command(plan_id, "approve", data, key, actor, factory)


@router.post("/route-plan-versions/{plan_id}/publish", response_model=RouteResponse)
def publish(
    plan_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.plan_command(plan_id, "publish", data, key, actor, factory)


@router.get("/routes", response_model=Page[RouteResponse])
def routes(
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    state: Annotated[
        str | None,
        Query(
            pattern=r"^(PUBLISHED|ACCEPTED|PICKUP_IN_PROGRESS|READY_TO_START|STARTED|EXECUTION_ENDED|OPERATIONALLY_COMPLETED|CANCELLED)$"
        ),
    ] = None,
    active: bool | None = None,
):
    return service.get_routes(
        actor, factory, page, page_size, state=state, active=active, settings=settings
    )


@router.get("/routes/{route_id}", response_model=RouteResponse)
def route(
    route_id: UUID,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
):
    return service.get_routes(actor, factory, route_id=route_id, settings=settings)


@router.post("/routes/{route_id}/accept", response_model=RouteResponse)
def accept(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(route_id, "accept", data, key, actor, factory)


@router.post("/routes/{route_id}/pickup", response_model=RouteResponse)
def pickup(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(route_id, "pickup", data, key, actor, factory)


@router.post("/routes/{route_id}/start", response_model=RouteResponse)
def start(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(route_id, "start", data, key, actor, factory)


@router.post("/routes/{route_id}/end-execution", response_model=RouteResponse)
def end_execution(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(route_id, "end-execution", data, key, actor, factory)


@router.post("/routes/{route_id}/complete-operation", response_model=RouteResponse)
def complete_operation(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(
        route_id, "complete-operation", data, key, actor, factory
    )


@router.post("/routes/{route_id}/cancel", response_model=RouteResponse)
def cancel(
    route_id: UUID,
    data: Versioned,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.route_command(route_id, "cancel", data, key, actor, factory)


@router.post(
    "/route-stops/{stop_id}/delivery-attempts",
    status_code=201,
    response_model=AttemptResponse,
)
def attempt(
    stop_id: UUID,
    data: AttemptCreate,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
):
    return service.create_attempt(stop_id, data, key, actor, factory)


@router.post("/delivery-attempts/{attempt_id}/receiver-authorizations")
def receiver_authorization(
    attempt_id: UUID,
    data: ReceiverAuthorization,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    return service.authorize_receiver(attempt_id, data, actor, factory, settings)


@router.post(
    "/delivery-attempts/{attempt_id}/evidence",
    status_code=201,
    response_model=EvidenceResponse,
)
def upload(
    attempt_id: UUID,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
    kind: Literal["PHOTO", "SIGNATURE"],
    file: Annotated[UploadFile, File()],
):
    content = file.file.read(service.MAX_EVIDENCE_BYTES + 1)
    return service.upload_evidence(attempt_id, kind, content, actor, factory, settings)


@router.get("/delivery-evidence/{evidence_id}")
def evidence(
    evidence_id: UUID,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
):
    content, mime = service.evidence(evidence_id, actor, factory, settings)
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )


@router.post("/delivery-attempts/{attempt_id}/complete")
def complete(
    attempt_id: UUID,
    data: ProofInput,
    key: Key,
    actor: CurrentUser,
    factory: SessionFactory,
    settings: ApplicationSettings,
):
    return service.complete_attempt(attempt_id, data, key, actor, factory, settings)


@router.get("/delivery-attempts/{attempt_id}/proof")
def proof(attempt_id: UUID, actor: CurrentUser, factory: SessionFactory):
    return service.get_proof(attempt_id, actor, factory)


@router.get("/supplier-orders/{order_id}/timeline")
def timeline(
    order_id: UUID,
    actor: CurrentUser,
    factory: SessionFactory,
    page: PageNumber = 1,
    page_size: PageSize = 25,
):
    return service.timeline(order_id, actor, factory, page, page_size)
