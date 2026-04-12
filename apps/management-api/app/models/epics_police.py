import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EpicsPoliceDecision(Base):
    __tablename__ = "epics_police_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_monday: Mapped[date] = mapped_column(Date, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The orphan ticket
    orphan_identifier: Mapped[str] = mapped_column(String(50), nullable=False)
    orphan_labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    orphan_squad: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # What was suggested
    suggested_parent_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    suggested_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_signals: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    match_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # What the user did
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_parent_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Whether this was inferred by the skill (vs explicit UI action)
    inferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_epics_police_decisions_week", "week_monday"),
        Index("ix_epics_police_decisions_orphan", "orphan_identifier"),
        Index("ix_epics_police_decisions_decision", "decision"),
    )
