from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from rotalog.core.contracts import Versioned
from rotalog.core.data_mode import is_synthetic_data


class ProductImageAttach(Versioned):
    file_id: UUID
    alt_text: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""


class ProductImageOrder(Versioned):
    image_ids: list[UUID] = Field(min_length=1, max_length=20)


class ProductImageResponse(BaseModel):
    id: UUID
    file_id: UUID
    position: int
    is_primary: bool
    alt_text: str
    mime_type: str
    size_bytes: int
    sha256: str
    width: int
    height: int
    content_url: str
    requires_authentication: Literal[True] = True


class ProductGallery(BaseModel):
    product_id: UUID
    product_version: int
    images: list[ProductImageResponse]
    primary_image_id: UUID | None
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
