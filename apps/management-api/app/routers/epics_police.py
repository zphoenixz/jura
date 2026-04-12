"""Epics Police router: analysis storage, decisions, learnings, and interactive UI."""

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.epics_police import (
    DecisionBatchInput,
    DecisionRead,
    DistillResponse,
    LearningsResponse,
)
from app.services.config_service import get_config_value, upsert_config
from app.services.epics_police_service import (
    distill_learnings,
    get_decisions,
    get_stored_learnings,
    store_decisions,
)

router = APIRouter(tags=["epics-police"])

# Bundled HTML file — lives in static/ next to the app package
_BUNDLED_HTML = Path(__file__).resolve().parents[2] / "static" / "epics-police.html"

ANALYSIS_SOURCE = "epics_police"
ANALYSIS_KEY = "latest_analysis"


# ── Analysis endpoints (existing) ────────────────────────────────


@router.post("/api/v1/epics-police/analysis")
async def store_analysis(body: Any = Body(...), db: AsyncSession = Depends(get_db)):
    """Store the latest epics police analysis JSON."""
    await upsert_config(db, ANALYSIS_SOURCE, ANALYSIS_KEY, body)
    await db.commit()
    return {"status": "ok", "stored_at": datetime.now(timezone.utc).isoformat()}


@router.get("/api/v1/epics-police/analysis")
async def get_analysis(db: AsyncSession = Depends(get_db)):
    """Return the latest stored analysis JSON."""
    value = await get_config_value(db, ANALYSIS_SOURCE, ANALYSIS_KEY)
    if value is None:
        raise HTTPException(status_code=404, detail={"error": "No analysis stored yet", "code": "not_found"})
    return value


# ── Decision endpoints ───────────────────────────────────────────


@router.post("/api/v1/epics-police/decisions")
async def post_decisions(
    body: DecisionBatchInput,
    db: AsyncSession = Depends(get_db),
):
    """Store a batch of accept/reject/redirect/manual decisions."""
    records = await store_decisions(db, body.decisions)
    await db.commit()
    return {
        "status": "ok",
        "stored": len(records),
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/api/v1/epics-police/decisions")
async def list_decisions(
    week: date | None = Query(None),
    decision: str | None = Query(None),
    limit: int = Query(500, le=5000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Query stored decisions with optional week and decision type filters."""
    records, total = await get_decisions(db, week, decision, limit, offset)
    return PaginatedResponse(
        items=[DecisionRead.model_validate(r) for r in records],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Learnings endpoints ──────────────────────────────────────────


@router.get("/api/v1/epics-police/learnings")
async def get_learnings(db: AsyncSession = Depends(get_db)):
    """Return the latest distilled learnings (weights, thresholds, patterns)."""
    learnings = await get_stored_learnings(db)
    if learnings is None:
        return LearningsResponse(
            learned_weights={
                "label_overlap": 35.0,
                "title_overlap": 25.0,
                "description_overlap": 20.0,
                "squad_match": 10.0,
                "notion_match": 10.0,
            },
            learned_thresholds={
                "pass1_lock": 70.0,
                "pass1_ambiguous_floor": 40.0,
                "feature_match": 60.0,
                "bug_matched": 70.0,
                "bug_suggested_floor": 40.0,
            },
            sufficient_data=False,
        )
    return learnings


@router.post("/api/v1/epics-police/distill")
async def trigger_distill(db: AsyncSession = Depends(get_db)):
    """Recompute learnings from all stored decisions."""
    learnings = await distill_learnings(db)
    await db.commit()
    return DistillResponse(
        distilled_at=learnings.last_distilled or datetime.now(timezone.utc),
        total_decisions=learnings.total_decisions,
        weeks_covered=learnings.weeks_covered,
        sufficient_data=learnings.sufficient_data,
        weights_changed=learnings.learned_weights != {
            "label_overlap": 35.0,
            "title_overlap": 25.0,
            "description_overlap": 20.0,
            "squad_match": 10.0,
            "notion_match": 10.0,
        },
        learned_weights=learnings.learned_weights,
        learned_thresholds=learnings.learned_thresholds,
    )


# ── UI serving ───────────────────────────────────────────────────


@router.get("/epics-police", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    """Serve the interactive Epics Police UI."""
    # Prefer env override if set, otherwise use bundled file
    override = settings.epics_police_html_path
    html_path = os.path.expanduser(override) if override else str(_BUNDLED_HTML)
    if not os.path.isfile(html_path):
        return HTMLResponse(
            content="<h1>Epics Police UI not found</h1>"
            f"<p>Expected at: <code>{html_path}</code></p>",
            status_code=404,
        )
    return FileResponse(html_path, media_type="text/html")
