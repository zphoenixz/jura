from datetime import date
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

T = TypeVar("T")


class SourceEnum(str, Enum):
    slack = "slack"
    linear = "linear"
    meets = "meets"
    epics = "epics"
    general = "general"
    epics_police = "epics_police"


class WeekParam(BaseModel):
    week: date | None = None


class WeekResponse(BaseModel):
    monday: date
    sunday: date
    week_label: str
    month_dir: str


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    error: str
    code: str
    detail: Any = None


class FetchSummary(BaseModel):
    week_label: str
    monday: date
    sunday: date
    warnings: list[str] = []
