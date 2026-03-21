from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week_utils import resolve_week
from app.models.week import Week


async def get_or_create_week(db: AsyncSession, week_date: date | None) -> Week:
    monday, sunday = resolve_week(week_date)
    result = await db.execute(select(Week).where(Week.monday_date == monday))
    week = result.scalar_one_or_none()
    if week is None:
        week = Week(monday_date=monday)
        db.add(week)
        await db.flush()
    return week


async def get_week(db: AsyncSession, week_date: date | None) -> Week | None:
    """Look up a week without creating it. Returns None if the week doesn't exist."""
    monday, _ = resolve_week(week_date)
    result = await db.execute(select(Week).where(Week.monday_date == monday))
    return result.scalar_one_or_none()
