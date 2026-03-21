import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SlackMessage(Base):
    __tablename__ = "slack_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weeks.id"), nullable=False
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    slack_ts: Mapped[str] = mapped_column(String(50), nullable=False)
    thread_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_dm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_thread_reply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reactions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("week_id", "channel_id", "slack_ts", name="uq_slack_messages_week_channel_ts"),
        Index("ix_slack_messages_week_channel", "week_id", "channel"),
        Index("ix_slack_messages_week_is_dm", "week_id", "is_dm"),
        Index("ix_slack_messages_week_person", "week_id", "person_id"),
    )
