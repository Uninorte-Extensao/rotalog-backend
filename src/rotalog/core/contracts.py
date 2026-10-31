"""Tipos comuns de entrada, paginação e conflitos versionados."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rotalog.core.data_mode import data_label, is_synthetic_data
from rotalog.core.errors import RotaLogError

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)
]
Quantity = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
PositiveQuantity = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Versioned(Input):
    expected_version: Annotated[int, Field(strict=True, ge=1)]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
    label: str = Field(default_factory=data_label)


def fail(code: str, message: str, status: int = 409) -> None:
    raise RotaLogError(code=code, message=message, status_code=status)


def check_version(actual: int, expected: int) -> None:
    if actual != expected:
        fail("VERSION_CONFLICT", "Versão desatualizada; consulte o recurso novamente.")
