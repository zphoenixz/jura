from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import FetchSummary


class EpicSubPageInput(BaseModel):
    notion_page_id: str
    title: str
    content: str = ""


class EpicInput(BaseModel):
    notion_page_id: str
    title: str
    status: str
    team: list[str] = []
    pm_lead: str | None = None
    sort_order: int | None = None
    dates: dict | None = None
    content: str = ""
    properties: dict = {}
    sub_pages: list[EpicSubPageInput] = []


class EpicsPushPayload(BaseModel):
    week: str | None = None
    epics: list[EpicInput]


class EpicSubPageRead(BaseModel):
    id: UUID
    notion_page_id: str
    title: str
    content: str | None = None

    class Config:
        from_attributes = True


class EpicRead(BaseModel):
    id: UUID
    notion_page_id: str
    title: str
    status: str
    team: Any | None = None
    pm_lead: str | None = None
    sort_order: int | None = None
    dates: Any | None = None
    content: str | None = None
    properties: Any | None = None
    sub_pages: list[EpicSubPageRead] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EpicsFetchSummary(FetchSummary):
    epics: int
    sub_pages: int
