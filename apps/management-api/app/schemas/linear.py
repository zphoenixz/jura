from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import FetchSummary


class LinearCommentRead(BaseModel):
    id: UUID
    linear_comment_id: str | None = None
    person_id: UUID | None = None
    author_name: str
    body: str
    linear_created_at: datetime | None = None

    class Config:
        from_attributes = True


class LinearTicketRead(BaseModel):
    id: UUID
    person_id: UUID | None = None
    linear_id: str
    identifier: str
    title: str
    description: str | None = None
    status: str
    status_type: str
    priority: int
    priority_label: str
    labels: Any | None = None
    points: int | None = None
    cycle_number: int | None = None
    cycle_name: str | None = None
    in_cycle: bool = True
    parent_identifier: str | None = None
    child_identifiers: Any | None = None
    attachments: Any | None = None
    url: str | None = None
    linear_created_at: datetime | None = None
    linear_updated_at: datetime | None = None
    comments: list[LinearCommentRead] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LinearFetchSummary(FetchSummary):
    tickets: int
    comments: int
    cycle_number: int | None = None


class LinearTicketPatch(BaseModel):
    """Request body for PATCH /api/v1/linear/tickets/{identifier}"""
    parent: str | None = None
    children: list[str] | None = None
    title: str | None = None
    description: str | None = None
    labels: list[str] | None = None
    status: str | None = None
    assignee: UUID | None = None
    priority: int | None = None
    points: int | None = None


class LinearTicketCreate(BaseModel):
    """Request body for POST /api/v1/linear/tickets"""
    title: str
    parent: str | None = None
    description: str | None = None
    labels: list[str] | None = None
    status: str | None = None
    assignee: UUID | None = None
    priority: int = 0
    points: int | None = None


class LinearMutationOp(BaseModel):
    """Single Linear API operation result"""
    identifier: str
    op: str
    value: str | None = None
    status: str
    error: str | None = None


class LinearMutationResponse(BaseModel):
    """Response for PATCH/POST linear tickets"""
    target: str
    linear_ops: list[LinearMutationOp] = []
    refreshed_at: datetime | None = None
    items: list[LinearTicketRead] = []
    total: int = 0
