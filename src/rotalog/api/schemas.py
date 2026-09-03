"""Schemas HTTP compartilhados pelo bootstrap da API."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Resposta do endpoint de saúde."""

    status: Literal["ok"]

