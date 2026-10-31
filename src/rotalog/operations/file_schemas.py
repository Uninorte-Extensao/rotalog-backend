"""Contratos de upload privado da demo e consulta sem paths físicos."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rotalog.core.contracts import Input
from rotalog.core.data_mode import is_synthetic_data

FilePurpose = Literal[
    "PRODUCT_IMAGE", "DELIVERY_PHOTO", "DELIVERY_SIGNATURE", "CARGO_ISSUE_PHOTO"
]


class UploadIntent(Input):
    purpose: FilePurpose
    product_id: UUID | None = None
    attempt_id: UUID | None = None
    route_id: UUID | None = None
    size_bytes: Annotated[int, Field(strict=True, gt=0, le=8388608)]
    mime_type: Literal["image/png", "image/jpeg"]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def context(self):
        expected = (
            "product_id"
            if self.purpose == "PRODUCT_IMAGE"
            else "route_id"
            if self.purpose == "CARGO_ISSUE_PHOTO"
            else "attempt_id"
        )
        values = {
            "product_id": self.product_id,
            "attempt_id": self.attempt_id,
            "route_id": self.route_id,
        }
        if values[expected] is None or sum(v is not None for v in values.values()) != 1:
            raise ValueError(
                "Informe somente o contexto exigido pela finalidade do arquivo"
            )
        return self


class FileStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    file_id: UUID
    purpose: FilePurpose
    product_id: UUID | None
    attempt_id: UUID | None = None
    route_id: UUID | None = None
    status: str
    size_bytes: int
    mime_type: str
    sha256: str
    expires_at: datetime
    rejection_reason: str | None
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
    scan_mode: Literal["SYNTHETIC_IMAGE_VALIDATION_ONLY", "IMAGE_VALIDATION_ONLY"] = (
        Field(
            default_factory=lambda: (
                "SYNTHETIC_IMAGE_VALIDATION_ONLY"
                if is_synthetic_data()
                else "IMAGE_VALIDATION_ONLY"
            )
        )
    )


class UploadIntentResponse(FileStatus):
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    requires_authentication: Literal[True] = True
    max_bytes: int
    max_pixels: int


class DownloadAccess(BaseModel):
    file_id: UUID
    download_url: str
    method: Literal["GET"] = "GET"
    headers: dict[str, str]
    expires_at: datetime
    requires_authentication: Literal[True] = True
