import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LinearTicket(Base):
    __tablename__ = "linear_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weeks.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id"), nullable=True
    )
    linear_id: Mapped[str] = mapped_column(String(100), nullable=False)
    identifier: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(100), nullable=False)
    status_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_label: Mapped[str] = mapped_column(String(50), nullable=False)
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent_identifier: Mapped[str | None] = mapped_column(String(50), nullable=True)
    child_identifiers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attachments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linear_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    linear_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    comments: Mapped[list["LinearComment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("week_id", "linear_id", name="uq_linear_tickets_week_linear"),
        Index("ix_linear_tickets_week_status_type", "week_id", "status_type"),
        Index("ix_linear_tickets_week_person", "week_id", "person_id"),
        Index("ix_linear_tickets_week_identifier", "week_id", "identifier"),
    )


class LinearComment(Base):
    __tablename__ = "linear_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("linear_tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    linear_comment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    linear_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    ticket: Mapped["LinearTicket"] = relationship(back_populates="comments")
