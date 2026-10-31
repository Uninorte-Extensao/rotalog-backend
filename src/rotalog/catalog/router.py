from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from rotalog.catalog import service
from rotalog.catalog.schemas import (
    CategoryCreate,
    CategoryResponse,
    OfferCreate,
    OfferFilters,
    OfferResponse,
    PriceChange,
    ProductCreate,
    ProductPatch,
    ProductResponse,
)
from rotalog.core.contracts import Page, Versioned
from rotalog.identity.dependencies import CurrentUser, SessionFactory

router = APIRouter(tags=["catálogo"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/categories", response_model=Page[CategoryResponse])
def categories(
    actor: CurrentUser,
    factory: SessionFactory,
    page: PageNumber = 1,
    page_size: PageSize = 25,
):
    return service.categories(None, actor, factory, page, page_size)


@router.post("/admin/categories", response_model=CategoryResponse, status_code=201)
def create_category(data: CategoryCreate, actor: CurrentUser, factory: SessionFactory):
    return service.categories(data, actor, factory)


@router.get("/admin/products", response_model=Page[ProductResponse])
def admin_products(
    actor: CurrentUser,
    factory: SessionFactory,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
    publication_state: Literal["DRAFT", "PUBLISHED", "UNPUBLISHED"] | None = None,
):
    return service.list_products(
        actor,
        factory,
        page,
        page_size,
        search,
        admin=True,
        publication_state=publication_state,
    )


@router.patch("/admin/products/{product_id}", response_model=ProductResponse)
def edit_product(
    product_id: UUID, data: ProductPatch, actor: CurrentUser, factory: SessionFactory
):
    return service.edit_product(product_id, data, actor, factory)


@router.post("/admin/products/{product_id}/unpublish", response_model=ProductResponse)
def unpublish(
    product_id: UUID, data: Versioned, actor: CurrentUser, factory: SessionFactory
):
    return service.unpublish_product(product_id, data.expected_version, actor, factory)


@router.post("/admin/products", response_model=ProductResponse, status_code=201)
def create_product(data: ProductCreate, actor: CurrentUser, factory: SessionFactory):
    return service.create_product(data, actor, factory)


@router.post("/admin/products/{product_id}/publish", response_model=ProductResponse)
def publish(
    product_id: UUID, data: Versioned, actor: CurrentUser, factory: SessionFactory
):
    return service.publish_product(product_id, data.expected_version, actor, factory)


@router.get("/products", response_model=Page[ProductResponse])
def products(
    actor: CurrentUser,
    factory: SessionFactory,
    page: PageNumber = 1,
    page_size: PageSize = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
):
    return service.list_products(actor, factory, page, page_size, search)


@router.get("/products/{product_id}", response_model=ProductResponse)
def product(product_id: UUID, actor: CurrentUser, factory: SessionFactory):
    return service.list_products(actor, factory, 1, 25, product_id=product_id)


@router.post(
    "/suppliers/{supplier_id}/offers", response_model=OfferResponse, status_code=201
)
def create_offer(
    supplier_id: UUID, data: OfferCreate, actor: CurrentUser, factory: SessionFactory
):
    return service.create_offer(supplier_id, data, actor, factory)


@router.get("/offers", response_model=Page[OfferResponse])
def offers(
    actor: CurrentUser,
    factory: SessionFactory,
    filters: Annotated[OfferFilters, Depends()],
    page: PageNumber = 1,
    page_size: PageSize = 25,
):
    return service.list_offers(filters, actor, factory, page, page_size)


@router.post("/offers/{offer_id}/price-changes", response_model=OfferResponse)
def price(
    offer_id: UUID, data: PriceChange, actor: CurrentUser, factory: SessionFactory
):
    return service.edit_offer(offer_id, data, data.expected_version, actor, factory)


@router.post("/offers/{offer_id}/pause", response_model=OfferResponse)
def pause(offer_id: UUID, data: Versioned, actor: CurrentUser, factory: SessionFactory):
    return service.edit_offer(offer_id, None, data.expected_version, actor, factory)
