from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fetch_log import FetchLog


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def start_fetch_log(db: AsyncSession, week_id: UUID, source: str) -> FetchLog:
    log = FetchLog(
        week_id=week_id,
        source=source,
        started_at=_utcnow(),
        status="started",
    )
    db.add(log)
    await db.flush()
    return log


async def complete_fetch_log(
    db: AsyncSession,
    log: FetchLog,
    *,
    status: str = "success",
    record_count: int = 0,
    warnings: list[str] | None = None,
) -> FetchLog:
    log.completed_at = _utcnow()
    log.status = status
    log.record_count = record_count
    log.warnings = warnings
    await db.flush()
    return log


async def is_fetch_in_progress(db: AsyncSession, week_id: UUID, source: str) -> bool:
    # Clean up stale "started" logs older than 15 minutes (crashed fetches)
    stale_cutoff = _utcnow() - timedelta(minutes=15)
    await db.execute(
        update(FetchLog)
        .where(FetchLog.status == "started", FetchLog.started_at < stale_cutoff)
        .values(status="failed", completed_at=_utcnow())
    )

    result = await db.execute(
        select(FetchLog).where(
            FetchLog.week_id == week_id,
            FetchLog.source == source,
            FetchLog.status == "started",
        )
    )
    return result.scalar_one_or_none() is not None


async def get_latest_fetch_logs(db: AsyncSession) -> dict[str, FetchLog | None]:
    sources = ["slack", "linear", "meets", "epics"]
    result = {}
    for source in sources:
        stmt = (
            select(FetchLog)
            .where(FetchLog.source == source)
            .order_by(FetchLog.created_at.desc())
            .limit(1)
        )
        row = await db.execute(stmt)
        result[source] = row.scalar_one_or_none()
    return result
