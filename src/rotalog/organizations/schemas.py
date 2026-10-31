from uuid import UUID

from pydantic import BaseModel


class SupplierResponse(BaseModel):
    id: UUID
    name: str
    is_synthetic: bool
