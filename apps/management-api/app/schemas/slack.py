from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import FetchSummary


class SlackMessageRead(BaseModel):
    id: UUID
    person_id: UUID | None = None
    channel: str
    channel_id: str
    content: str
    slack_ts: str
    thread_ts: str | None = None
    is_dm: bool
    is_thread_reply: bool
    reactions: Any | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SlackFetchSummary(FetchSummary):
    messages: int
    threads: int
