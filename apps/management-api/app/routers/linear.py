from datetime import date
from uuid import UUID

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.mentions import build_linear_mention_map, replace_linear_mentions
from app.core.week_utils import is_current_week, resolve_week, week_label
from app.database import get_db
from app.models.linear import LinearTicket
from app.schemas.common import PaginatedResponse
from app.schemas.linear import (
    EpicProgressEntry,
    EpicProgressPoints,
    EpicProgressRequest,
    EpicProgressResponse,
    LinearFetchSummary,
    LinearMutationResponse,
    LinearTicketCreate,
    LinearTicketPatch,
    LinearTicketRead,
)
from app.services.fetch_log_service import complete_fetch_log, is_fetch_in_progress, start_fetch_log
from app.services.linear_fetcher import fetch_and_store_linear
from app.services.linear_writer import create_ticket, patch_ticket
from app.services.linear_progress import fetch_epic_progress
from app.services.week_service import get_or_create_week, get_week

router = APIRouter(prefix="/api/v1/linear", tags=["linear"])


async def _resolve_ticket_mentions(
    db: AsyncSession, tickets: list[LinearTicket]
) -> list[LinearTicketRead]:
    """Resolve @mentions in ticket descriptions and comments."""
    all_texts: list[str] = []
    for t in tickets:
        if t.description:
            all_texts.append(t.description)
        for c in t.comments:
            if c.body:
                all_texts.append(c.body)
    mention_map = await build_linear_mention_map(db, all_texts)

    items = []
    for t in tickets:
        read = LinearTicketRead.model_validate(t)
        if read.description:
            read.description = replace_linear_mentions(read.description, mention_map)
        for c in read.comments:
            if c.body:
                c.body = replace_linear_mentions(c.body, mention_map)
        items.append(read)
    return items


@router.post("/fetch")
async def fetch_linear(week: date | None = Query(None), db: AsyncSession = Depends(get_db)):
    w = await get_or_create_week(db, week)
    monday, sunday = resolve_week(w.monday_date)

    if await is_fetch_in_progress(db, w.id, "linear"):
        raise HTTPException(status_code=409, detail={"error": "Linear fetch already in progress for this week", "code": "fetch_in_progress"})

    # Protect historical snapshots — Linear returns current state, not point-in-time
    if not is_current_week(monday):
        existing = await db.execute(select(func.count()).select_from(LinearTicket).where(LinearTicket.week_id == w.id))
        if existing.scalar() > 0:
            raise HTTPException(
                status_code=409,
                detail={"error": "Historical week already has linear data. Re-fetching would overwrite the point-in-time snapshot with current ticket states.", "code": "historical_protected"},
            )

    log = await start_fetch_log(db, w.id, "linear")

    ticket_count, comment_count, cycle_number, warnings = await fetch_and_store_linear(db, w.id, monday, sunday)

    status = "success" if not warnings else "partial"
    await complete_fetch_log(db, log, status=status, record_count=ticket_count, warnings=warnings or None)
    await db.commit()

    return LinearFetchSummary(
        week_label=week_label(monday, sunday), monday=monday, sunday=sunday,
        tickets=ticket_count, comments=comment_count, cycle_number=cycle_number, warnings=warnings,
    )


@router.get("")
async def get_linear(
    week: date | None = Query(None), status: str | None = None,
    status_type: str | None = None, person_id: UUID | None = None,
    label: str | None = None, priority: int | None = None,
    identifier: str | None = None, limit: int = Query(500, le=5000),
    offset: int = 0, db: AsyncSession = Depends(get_db),
):
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return PaginatedResponse(items=[], total=0, limit=limit, offset=offset)

    query = select(LinearTicket).where(LinearTicket.week_id == w.id).options(selectinload(LinearTicket.comments))
    count_query = select(func.count()).select_from(LinearTicket).where(LinearTicket.week_id == w.id)

    if status:
        query = query.where(LinearTicket.status == status)
        count_query = count_query.where(LinearTicket.status == status)
    if status_type:
        query = query.where(LinearTicket.status_type == status_type)
        count_query = count_query.where(LinearTicket.status_type == status_type)
    if person_id:
        query = query.where(LinearTicket.person_id == person_id)
        count_query = count_query.where(LinearTicket.person_id == person_id)
    if label:
        query = query.where(LinearTicket.labels.contains([label]))
        count_query = count_query.where(LinearTicket.labels.contains([label]))
    if priority is not None:
        query = query.where(LinearTicket.priority == priority)
        count_query = count_query.where(LinearTicket.priority == priority)
    if identifier:
        query = query.where(LinearTicket.identifier == identifier)
        count_query = count_query.where(LinearTicket.identifier == identifier)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    query = query.order_by(LinearTicket.priority, LinearTicket.identifier).limit(limit).offset(offset)
    result = await db.execute(query)
    tickets = list(result.unique().scalars().all())
    items = await _resolve_ticket_mentions(db, tickets)

    return PaginatedResponse(
        items=items, total=total, limit=limit, offset=offset,
    )


@router.get("/formatted")
async def get_linear_formatted(
    week: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return formatted markdown files for the week, one per ticket."""
    from app.services.formatters import format_linear_week
    monday, _ = resolve_week(week)
    w = await get_week(db, monday)
    if w is None:
        return []
    return await format_linear_week(db, w.id)


async def _refetch_and_respond(
    db: AsyncSession, target: str, ops: list,
) -> LinearMutationResponse:
    """Refetch current week from Linear and return fresh ticket data."""
    monday, sunday = resolve_week(None)
    w = await get_or_create_week(db, monday)
    await fetch_and_store_linear(db, w.id, monday, sunday)
    await db.commit()

    refreshed_at = datetime.now(timezone.utc)

    fresh_query = (
        select(LinearTicket)
        .where(LinearTicket.week_id == w.id)
        .options(selectinload(LinearTicket.comments))
    )
    fresh_result = await db.execute(fresh_query)
    fresh_tickets = list(fresh_result.unique().scalars().all())
    items = await _resolve_ticket_mentions(db, fresh_tickets)

    return LinearMutationResponse(
        target=target,
        linear_ops=ops,
        refreshed_at=refreshed_at,
        items=items,
        total=len(items),
    )


@router.patch("/tickets/{identifier}")
async def update_ticket(
    identifier: str,
    patch: LinearTicketPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update a Linear ticket's relationships and/or fields."""
    raw_body = await request.json()

    if not any(k in raw_body for k in (
        "parent", "children", "title", "description", "labels",
        "status", "assignee", "priority", "points",
    )):
        raise HTTPException(
            status_code=400,
            detail={"error": "At least one field must be provided", "code": "empty_patch"},
        )

    try:
        target, ops = await patch_ticket(db, identifier, patch, raw_body)
    except ValueError as e:
        error_str = str(e)
        if "cycle" in error_str.lower():
            raise HTTPException(status_code=409, detail={"error": error_str, "code": "cycle_detected"})
        if "not found" in error_str.lower():
            raise HTTPException(status_code=404, detail={"error": error_str, "code": "not_found"})
        raise HTTPException(status_code=400, detail={"error": error_str, "code": "validation_error"})

    return LinearMutationResponse(
        target=target,
        linear_ops=ops,
        refreshed_at=datetime.now(timezone.utc),
    )


@router.post("/tickets")
async def create_new_ticket(
    create: LinearTicketCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new Linear ticket."""
    try:
        target, ops = await create_ticket(db, create)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": str(e), "code": "validation_error"},
        )

    response = await _refetch_and_respond(db, target, ops)

    # Guard against Linear eventual consistency: if the newly created ticket
    # didn't appear in the cycle refetch, fetch it individually and insert it.
    if target and target != "unknown":
        from app.services.linear_writer import _ensure_ticket_in_db
        await _ensure_ticket_in_db(db, target)
        await db.commit()

    return response


@router.post("/epics/progress", response_model=EpicProgressResponse)
async def epic_progress(body: EpicProgressRequest):
    """Roll up descendant story points per declared epic, cycle-agnostic.

    Returns a mapping of identifier → {points, descendant_count, missing_estimates}.
    Per-epic failures yield `null` for that identifier and a `warnings` entry;
    callers can render the rest of the batch normally.
    """
    if not body.identifiers:
        raise HTTPException(
            status_code=422,
            detail={"error": "identifiers must be non-empty", "code": "empty_identifiers"},
        )
    if len(body.identifiers) > 100:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "identifiers list too large (max 100)",
                "code": "too_many_identifiers",
            },
        )

    results, warnings = await fetch_epic_progress(body.identifiers)

    if all(v is None for v in results.values()):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "Linear lookups failed for the entire batch",
                "code": "linear_unavailable",
                "warnings": warnings,
            },
        )

    typed: dict[str, EpicProgressEntry | None] = {}
    for ident, value in results.items():
        if value is None:
            typed[ident] = None
        else:
            typed[ident] = EpicProgressEntry(
                points=EpicProgressPoints(**value.points),
                descendant_count=value.descendant_count,
                missing_estimates=value.missing_estimates,
            )
    return EpicProgressResponse(results=typed, warnings=warnings)
