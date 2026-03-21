from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ConfigRead(BaseModel):
    id: UUID
    source: str
    key: str
    value: Any
    updated_at: datetime

    class Config:
        from_attributes = True


class ConfigUpdate(BaseModel):
    value: Any
