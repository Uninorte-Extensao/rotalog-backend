"""Endpoint de disponibilidade da API."""

from fastapi import APIRouter

from rotalog.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Informa se o processo da API está disponível."""

    return HealthResponse(status="ok")
