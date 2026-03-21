from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from app.core.config import settings
from app.core.http_client import resilient_request
from app.core.mentions import build_slack_mention_map, replace_slack_mentions
from app.core.week_utils import is_current_week, resolve_week, week_label
from app.database import get_db
from app.models.slack import SlackMessage
from app.schemas.common import PaginatedResponse
from app.schemas.slack import SlackFetchSummary, SlackMessageRead
from app.services.config_service import get_config_value, upsert_config
from app.services.fetch_log_service import complete_fetch_log, is_fetch_in_progress, start_fetch_log
from app.services.slack_fetcher import fetch_and_store_slack
from app.services.week_service import get_or_create_week, get_week

router = APIRouter(prefix="/api/v1/slack", tags=["slack"])


class AddChannelRequest(BaseModel):
    name: str


@router.post("/channels")
async def add_channel(body: AddChannelRequest, db: AsyncSession = Depends(get_db)):
    """Look up a Slack channel by name, resolve its ID, and add to watched_channels.

    Uses search.messages as a fast lookup (avoids paginating 1000+ channels).
    Falls back to conversations.list only if search finds nothing.
    """
    name = body.name.strip().lstrip("#")

    # Check if already watched
    watched = await get_config_value(db, "slack", "watched_channels", {})
    if name in watched:
        return {"name": name, "channel_id": watched[name], "status": "already_watched"}

    headers = {"Authorization": f"Bearer {settings.slack_bot_token}", "Content-Type": "application/json"}
    channel_id = None

    # Search for a message in the channel (returns channel ID in results)
    for query in [f"in:#{name}", f"in:{name}"]:
        r = await resilient_request(
            "GET", "https://slack.com/api/search.messages",
            headers=headers, params={"query": query, "count": 1},
        )
        data = r.json()
        if data.get("ok"):
            for m in (data.get("messages", {}).get("matches", [])):
                ch = m.get("channel", {})
                if ch.get("name") == name:
                    channel_id = ch.get("id")
                    break
        if channel_id:
            break

    if not channel_id:
        raise HTTPException(
            status_code=404,
            detail={"error": f"Channel '{name}' not found. Provide the channel_id directly via PUT /api/v1/config/slack/watched_channels", "code": "channel_not_found"},
        )

    # Add to watched_channels
    watched[name] = channel_id
    await upsert_config(db, "slack", "watched_channels", watched)
    await db.commit()

    return {"name": name, "channel_id": channel_id, "status": "added"}


@router.post("/channels/by-id")
async def add_channel_by_id(name: str = Query(...), channel_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Add a channel directly with a known ID. No Slack API call needed."""
    watched = await get_config_value(db, "slack", "watched_channels", {})
    watched[name] = channel_id
    await upsert_config(db, "slack", "watched_channels", watched)
    await db.commit()
    return {"name": name, "channel_id": channel_id, "status": "added"}


@router.post("/fetch")
async def fetch_slack(week: date | None = Query(None), db: AsyncSession = Depends(get_db)):
    w = await get_or_create_week(db, week)
    monday, sunday = resolve_week(w.monday_date)

    if await is_fetch_in_progress(db, w.id, "slack"):
        raise HTTPException(status_code=409, detail={"error": "Slack fetch already in progress for this week", "code": "fetch_in_progress"})

    if not is_current_week(monday):
        existing = await db.execute(select(func.count()).select_from(SlackMessage).where(SlackMessage.week_id == w.id))
        if existing.scalar() > 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "Historical week already has slack data. Re-fetching would overwrite the point-in-time snapshot.", "code": "historical_protected"},
            )

    log = await start_fetch_log(db, w.id, "slack")

    msg_count, thread_count, warnings = await fetch_and_store_slack(db, w.id, monday, sunday)

    status = "success" if not warnings else "partial"
    await complete_fetch_log(db, log, status=status, record_count=msg_count, warnings=warnings or None)
    await db.commit()

    return SlackFetchSummary(
        week_label=week_label(monday, sunday), monday=monday, sunday=sunday,
        messages=msg_count, threads=thread_count, warnings=warnings,
    )


@router.get("")
async def get_slack(
    week: date | None = Query(None), channel: str | None = None,
    person_id: UUID | None = None, is_dm: bool | None = None,
    is_thread_reply: bool | None = None, limit: int = Query(500, le=5000),
    offset: int = 0, db: AsyncSession = Depends(get_db),
):
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return PaginatedResponse(items=[], total=0, limit=limit, offset=offset)

    query = select(SlackMessage).where(SlackMessage.week_id == w.id)
    count_query = select(func.count()).select_from(SlackMessage).where(SlackMessage.week_id == w.id)

    if channel:
        query = query.where(SlackMessage.channel == channel)
        count_query = count_query.where(SlackMessage.channel == channel)
    if person_id:
        query = query.where(SlackMessage.person_id == person_id)
        count_query = count_query.where(SlackMessage.person_id == person_id)
    if is_dm is not None:
        query = query.where(SlackMessage.is_dm == is_dm)
        count_query = count_query.where(SlackMessage.is_dm == is_dm)
    if is_thread_reply is not None:
        query = query.where(SlackMessage.is_thread_reply == is_thread_reply)
        count_query = count_query.where(SlackMessage.is_thread_reply == is_thread_reply)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(SlackMessage.slack_ts).limit(limit).offset(offset)
    result = await db.execute(query)
    messages = list(result.scalars().all())

    # Interpolate <@U...> mentions with human-readable names at GET time
    mention_map = await build_slack_mention_map(db, [m.content for m in messages])
    items = []
    for m in messages:
        read = SlackMessageRead.model_validate(m)
        read.content = replace_slack_mentions(read.content, mention_map)
        items.append(read)

    return PaginatedResponse(
        items=items, total=total, limit=limit, offset=offset,
    )


@router.get("/formatted")
async def get_slack_formatted(
    week: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return formatted markdown files for the week, one per channel/DM/mpim."""
    from app.services.formatters import format_slack_week
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return []
    return await format_slack_week(db, w.id)
