from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import FetchSummary


class MeetingAttendeeRead(BaseModel):
    id: UUID
    person_id: UUID | None = None
    name: str
    email: str | None = None

    class Config:
        from_attributes = True


class MeetingRead(BaseModel):
    id: UUID
    fireflies_id: str
    title: str
    meeting_date: datetime | None = None
    duration: int | None = None
    keywords: Any | None = None
    overview: str | None = None
    short_summary: str | None = None
    notes: str | None = None
    action_items: str | None = None
    attendees: list[MeetingAttendeeRead] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MeetsFetchSummary(FetchSummary):
    meetings: int
    excluded: int
