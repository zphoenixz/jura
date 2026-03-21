import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weeks.id"), nullable=False
    )
    fireflies_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    meeting_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    keywords: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    short_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    action_items: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    attendees: Mapped[list["MeetingAttendee"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "week_id", "fireflies_id", name="uq_meetings_week_fireflies"
        ),
        Index("ix_meetings_week_id", "week_id"),
    )


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    meeting: Mapped["Meeting"] = relationship(back_populates="attendees")

    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "email", name="uq_meeting_attendees_meeting_email"
        ),
    )
