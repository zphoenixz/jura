import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Epic(Base):
    __tablename__ = "epics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("weeks.id"), nullable=False
    )
    notion_page_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(100), nullable=False)
    team: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    pm_lead: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dates: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sub_pages: Mapped[list["EpicSubPage"]] = relationship(
        back_populates="epic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "week_id", "notion_page_id", name="uq_epics_week_notion_page"
        ),
        Index("ix_epics_week_status", "week_id", "status"),
    )


class EpicSubPage(Base):
    __tablename__ = "epic_sub_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    epic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("epics.id", ondelete="CASCADE"),
        nullable=False,
    )
    notion_page_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    epic: Mapped["Epic"] = relationship(back_populates="sub_pages")
