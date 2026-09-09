"""Ponto de entrada da aplicação FastAPI."""

from fastapi import FastAPI

from rotalog.api.schemas import HealthResponse

app = FastAPI(
    title="RotaLog API",
    description="API backend do marketplace B2B RotaLog.",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Informa se o processo da API está disponível."""
    return HealthResponse(status="ok")

