from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rotalog.catalog.image_schemas import ProductImageResponse
from rotalog.core.contracts import Input, Name, PositiveQuantity, Versioned
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.money import MoneyInput

CategoryCode = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[A-Z][A-Z0-9_]*$")
]
Gtin = Annotated[str, Field(pattern=r"^(?:[0-9]{8}|[0-9]{12,14})$")]


class CategoryCreate(Input):
    code: CategoryCode
    name: Name


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str


class ProductCreate(Input):
    name: Name
    brand: Name
    category: CategoryCode
    gtin: Gtin | None = None
    net_content: PositiveQuantity
    net_content_unit: Literal["g", "ml", "un"]
    packaging_type: Name
    packaging_quantity: PositiveQuantity
    gross_weight_grams: PositiveQuantity
    volume_cm3: PositiveQuantity
    variant_attributes: dict[str, str] = Field(default_factory=dict, max_length=20)


class ProductPatch(Versioned):
    name: Name | None = None
    brand: Name | None = None
    category: CategoryCode | None = None
    gtin: Gtin | None = None
    net_content: PositiveQuantity | None = None
    net_content_unit: Literal["g", "ml", "un"] | None = None
    packaging_type: Name | None = None
    packaging_quantity: PositiveQuantity | None = None
    gross_weight_grams: PositiveQuantity | None = None
    volume_cm3: PositiveQuantity | None = None
    variant_attributes: dict[str, str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def changes(self):
        fields = self.model_fields_set - {"expected_version"}
        if not fields:
            raise ValueError("Informe pelo menos um campo para editar")
        if any(getattr(self, field) is None for field in fields - {"gtin"}):
            raise ValueError("Somente gtin pode ser removido com null")
        return self


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    brand: str | None
    category: str | None
    gtin: str | None = None
    net_content: int | None
    net_content_unit: str | None
    packaging_type: str | None
    packaging_quantity: int | None
    gross_weight_grams: int | None
    volume_cm3: int | None
    variant_attributes: dict[str, str]
    id: UUID
    publication_state: str
    version: int
    demo_run_id: UUID
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
    images: list[ProductImageResponse] = Field(default_factory=list)
    primary_image: ProductImageResponse | None = None


class OfferCreate(Input):
    product_id: UUID
    warehouse_id: UUID
    unit_price: MoneyInput
    currency: Literal["BRL"] = "BRL"
    minimum_order_quantity: PositiveQuantity = 1


class PriceChange(Versioned):
    unit_price: MoneyInput
    currency: Literal["BRL"] = "BRL"
    minimum_order_quantity: PositiveQuantity = 1


class OfferResponse(BaseModel):
    id: UUID
    product_id: UUID
    product_name: str
    supplier_organization_id: UUID
    supplier_name: str
    warehouse_id: UUID
    region: str
    unit_price: str
    currency: Literal["BRL"] = "BRL"
    minimum_order_quantity: int
    available_quantity: int
    version: int
    is_active: bool
    demo_run_id: UUID
    is_synthetic: bool = Field(default_factory=is_synthetic_data)
    product_image: ProductImageResponse | None = None


class OfferFilters(BaseModel):
    product_id: UUID | None = None
    supplier_id: UUID | None = None
    region: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
