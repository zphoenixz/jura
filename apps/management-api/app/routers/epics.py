from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.week_utils import is_current_week, resolve_week, week_label
from app.database import get_db
from app.models.epic import Epic
from app.schemas.common import PaginatedResponse
from app.schemas.epic import EpicRead, EpicsFetchSummary, EpicsPushPayload
from app.services.epics_fetcher import fetch_and_store_epics
from app.services.epics_store import store_epics
from app.services.fetch_log_service import complete_fetch_log, is_fetch_in_progress, start_fetch_log
from app.services.week_service import get_or_create_week, get_week

router = APIRouter(prefix="/api/v1/epics", tags=["epics"])


@router.post("/fetch")
async def fetch_epics(
    body: EpicsPushPayload | None = None,
    week: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Fetch epics from Notion API (default) or push JSON body.

    - No body: fetches directly from Notion API using NOTION_API_KEY
    - With body: accepts pushed JSON (legacy push pattern)
    """
    # Determine week
    if body and body.week:
        week_date = date.fromisoformat(body.week)
    else:
        week_date = week
    w = await get_or_create_week(db, week_date)
    monday, sunday = resolve_week(w.monday_date)

    if await is_fetch_in_progress(db, w.id, "epics"):
        raise HTTPException(status_code=409, detail={"error": "Epics fetch already in progress for this week", "code": "fetch_in_progress"})

    # Protect historical snapshots — Notion has no versioning
    if not is_current_week(monday):
        existing = await db.execute(select(func.count()).select_from(Epic).where(Epic.week_id == w.id))
        if existing.scalar() > 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "Historical week already has epic data. Re-fetching would overwrite the point-in-time snapshot with current state.", "code": "historical_protected"},
            )

    log = await start_fetch_log(db, w.id, "epics")

    if body and body.epics:
        # Push mode (legacy)
        epic_count, sub_page_count = await store_epics(db, w.id, body.epics)
        warnings = []
    else:
        # Direct fetch from Notion API
        epic_count, sub_page_count, warnings = await fetch_and_store_epics(db, w.id, monday, sunday)

    status = "success" if not warnings else "partial"
    await complete_fetch_log(db, log, status=status, record_count=epic_count, warnings=warnings or None)
    await db.commit()

    return EpicsFetchSummary(
        week_label=week_label(monday, sunday),
        monday=monday,
        sunday=sunday,
        epics=epic_count,
        sub_pages=sub_page_count,
        warnings=warnings,
    )


@router.get("")
async def get_epics(
    week: date | None = Query(None),
    status: str | None = None,
    title: str | None = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    monday, sunday = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return PaginatedResponse(items=[], total=0, limit=limit, offset=offset)

    query = select(Epic).where(Epic.week_id == w.id).options(selectinload(Epic.sub_pages))
    count_query = select(func.count()).select_from(Epic).where(Epic.week_id == w.id)

    if status:
        query = query.where(Epic.status == status)
        count_query = count_query.where(Epic.status == status)
    if title:
        query = query.where(Epic.title.ilike(f"%{title}%"))
        count_query = count_query.where(Epic.title.ilike(f"%{title}%"))

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(Epic.sort_order.asc().nullslast(), Epic.title).limit(limit).offset(offset)
    result = await db.execute(query)

    return PaginatedResponse(
        items=[EpicRead.model_validate(e) for e in result.scalars().all()],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/formatted")
async def get_epics_formatted(
    week: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return formatted markdown files for the week, one per epic."""
    from app.services.formatters import format_epics_week
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return []
    return await format_epics_week(db, w.id)
