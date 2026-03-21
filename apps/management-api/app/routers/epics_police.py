"""Epics Police router: analysis storage + interactive UI serving."""

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database import get_db
from app.services.config_service import get_config_value, upsert_config

router = APIRouter(tags=["epics-police"])

ANALYSIS_SOURCE = "epics_police"
ANALYSIS_KEY = "latest_analysis"


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


@router.get("/epics-police", response_class=HTMLResponse, include_in_schema=False)
async def serve_ui():
    """Serve the interactive Epics Police UI."""
    html_path = os.path.expanduser(settings.epics_police_html_path)
    if not os.path.isfile(html_path):
        return HTMLResponse(
            content="<h1>Epics Police UI not found</h1>"
            f"<p>Expected at: <code>{html_path}</code></p>"
            "<p>Run the epics-police skill to create it, or check the path.</p>",
            status_code=404,
        )
    return FileResponse(html_path, media_type="text/html")
