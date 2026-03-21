from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.week_utils import is_current_week, resolve_week, week_label
from app.database import get_db
from app.models.meeting import Meeting, MeetingAttendee
from app.schemas.common import PaginatedResponse
from app.schemas.meeting import MeetingRead, MeetsFetchSummary
from app.services.fetch_log_service import complete_fetch_log, is_fetch_in_progress, start_fetch_log
from app.services.meets_fetcher import fetch_and_store_meets
from app.services.week_service import get_or_create_week, get_week

router = APIRouter(prefix="/api/v1/meets", tags=["meets"])


@router.post("/fetch")
async def fetch_meets(week: date | None = Query(None), db: AsyncSession = Depends(get_db)):
    w = await get_or_create_week(db, week)
    monday, sunday = resolve_week(w.monday_date)

    if await is_fetch_in_progress(db, w.id, "meets"):
        raise HTTPException(status_code=409, detail={"error": "Meets fetch already in progress for this week", "code": "fetch_in_progress"})

    if not is_current_week(monday):
        existing = await db.execute(select(func.count()).select_from(Meeting).where(Meeting.week_id == w.id))
        if existing.scalar() > 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "Historical week already has meets data. Re-fetching would overwrite the point-in-time snapshot.", "code": "historical_protected"},
            )

    log = await start_fetch_log(db, w.id, "meets")

    meeting_count, excluded_count, warnings = await fetch_and_store_meets(db, w.id, monday, sunday)

    status = "success" if not warnings else "partial"
    await complete_fetch_log(db, log, status=status, record_count=meeting_count, warnings=warnings or None)
    await db.commit()

    return MeetsFetchSummary(
        week_label=week_label(monday, sunday), monday=monday, sunday=sunday,
        meetings=meeting_count, excluded=excluded_count, warnings=warnings,
    )


@router.get("")
async def get_meets(
    week: date | None = Query(None), title: str | None = None,
    person_id: UUID | None = None, limit: int = Query(500, le=5000),
    offset: int = 0, db: AsyncSession = Depends(get_db),
):
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return PaginatedResponse(items=[], total=0, limit=limit, offset=offset)

    query = select(Meeting).where(Meeting.week_id == w.id).options(selectinload(Meeting.attendees))
    count_query = select(func.count()).select_from(Meeting).where(Meeting.week_id == w.id)

    if title:
        query = query.where(Meeting.title.ilike(f"%{title}%"))
        count_query = count_query.where(Meeting.title.ilike(f"%{title}%"))
    if person_id:
        query = query.join(MeetingAttendee).where(MeetingAttendee.person_id == person_id)
        count_query = count_query.join(MeetingAttendee).where(MeetingAttendee.person_id == person_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(Meeting.meeting_date).limit(limit).offset(offset)
    result = await db.execute(query)

    return PaginatedResponse(
        items=[MeetingRead.model_validate(m) for m in result.unique().scalars().all()],
        total=total, limit=limit, offset=offset,
    )


@router.get("/formatted")
async def get_meets_formatted(
    week: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return formatted markdown files for the week, one per meeting."""
    from app.services.formatters import format_meets_week
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return []
    return await format_meets_week(db, w.id)
