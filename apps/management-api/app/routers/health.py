from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week_utils import month_dir, week_label
from app.database import get_db
from app.models.epic import Epic
from app.models.fetch_log import FetchLog
from app.models.linear import LinearTicket
from app.models.meeting import Meeting
from app.models.slack import SlackMessage
from app.models.week import Week

router = APIRouter(prefix="/api/v1", tags=["utility"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(func.now()))
        db_status = "ok"
    except Exception:
        db_status = "error"

    sources = {}
    for source in ["slack", "linear", "meets", "epics"]:
        stmt = (
            select(FetchLog)
            .where(FetchLog.source == source)
            .order_by(FetchLog.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()
        if log:
            sources[source] = {
                "last_fetched": log.completed_at.isoformat() if log.completed_at else None,
                "last_status": log.status,
            }
        else:
            sources[source] = {"last_fetched": None, "last_status": None}

    return {"status": "ok" if db_status == "ok" else "degraded", "db": db_status, "sources": sources}


@router.get("/weeks")
async def list_weeks(db: AsyncSession = Depends(get_db)):
    weeks = await db.execute(select(Week).order_by(Week.monday_date.desc()))
    all_weeks = weeks.scalars().all()
    if not all_weeks:
        return []

    week_ids = [w.id for w in all_weeks]

    # Single aggregated query per source instead of N queries per week
    counts: dict[str, dict] = {w.id: {"slack": 0, "linear": 0, "meets": 0, "epics": 0} for w in all_weeks}
    for model, name in [(SlackMessage, "slack"), (LinearTicket, "linear"), (Meeting, "meets"), (Epic, "epics")]:
        stmt = (
            select(model.week_id, func.count())
            .where(model.week_id.in_(week_ids))
            .group_by(model.week_id)
        )
        rows = await db.execute(stmt)
        for week_id, count in rows.all():
            counts[week_id][name] = count

    result = []
    for w in all_weeks:
        monday = w.monday_date
        sunday = monday + timedelta(days=6)
        result.append({
            "monday": monday.isoformat(),
            "sunday": sunday.isoformat(),
            "week_label": week_label(monday, sunday),
            "month_dir": month_dir(monday),
            "sources": counts[w.id],
        })
    return result
