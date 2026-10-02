from pydantic import BaseModel
from typing import Optional, TypeVar, Generic

T = TypeVar('T')

class StandardResponse(BaseModel, Generic[T]):
    success: bool = True
    payload: Optional[T] = None

    class Config:
        from_attributes = True


class PaginationResponse(BaseModel):
    total: int
    page: int
    size: int
    total_pages: int
    has_next: Optional[bool]
    has_previous: Optional[bool]

    class Config:
        from_attributes = True


class MetaResponse(BaseModel):
    pagination : PaginationResponse

    class Config:
        from_attributes = True


class GenericMessageResponse(BaseModel):
    message: str

    class Config:
        from_attributes = True

