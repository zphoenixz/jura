from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PersonRead(BaseModel):
    id: UUID
    display_name: str
    email: str | None = None
    slack_user_id: str | None = None
    linear_user_id: str | None = None
    fireflies_name: str | None = None
    squad: str | None = None
    role: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PersonPatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    slack_user_id: str | None = None
    linear_user_id: str | None = None
    fireflies_name: str | None = None
    squad: str | None = None
    role: str | None = None
