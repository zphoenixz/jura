"""Fetch Notion epics via the Notion API and store in DB."""

import re
from datetime import date

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import resilient_request
from app.models.epic import Epic, EpicSubPage
from app.services.config_service import get_config_value

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers():
    return {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def _extract_db_id(url_or_id: str) -> str:
    """Extract database ID from URL or raw ID."""
    # Handle collection:// format
    if url_or_id.startswith("collection://"):
        raw = url_or_id.replace("collection://", "")
        return raw
    # Handle full URL
    m = re.search(r"([a-f0-9]{32})", url_or_id.replace("-", ""))
    if m:
        raw = m.group(1)
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return url_or_id


def _rich_text_to_str(rich_text_list: list) -> str:
    """Convert Notion rich_text array to plain string."""
    return "".join(rt.get("plain_text", "") for rt in (rich_text_list or []))


def _extract_property(props: dict, name: str) -> str | None:
    """Extract a string value from a Notion property."""
    prop = props.get(name)
    if not prop:
        return None
    ptype = prop.get("type", "")
    if ptype == "title":
        return _rich_text_to_str(prop.get("title", []))
    if ptype == "rich_text":
        return _rich_text_to_str(prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if ptype == "status":
        sel = prop.get("status")
        return sel.get("name") if sel else None
    if ptype == "multi_select":
        return [s.get("name", "") for s in prop.get("multi_select", [])]
    if ptype == "number":
        return prop.get("number")
    if ptype == "date":
        d = prop.get("date")
        return d.get("start") if d else None
    if ptype == "people":
        return [p.get("name", "") for p in prop.get("people", [])]
    if ptype == "relation":
        return [r.get("id", "") for r in prop.get("relation", [])]
    if ptype == "url":
        return prop.get("url")
    return None


def _blocks_to_markdown(blocks: list) -> str:
    """Convert Notion blocks to markdown."""
    lines = []
    for block in blocks:
        btype = block.get("type", "")
        data = block.get(btype, {})

        if btype == "paragraph":
            lines.append(_rich_text_to_str(data.get("rich_text", [])))
        elif btype in ("heading_1", "heading_2", "heading_3"):
            level = btype[-1]
            text = _rich_text_to_str(data.get("rich_text", []))
            lines.append(f"{'#' * int(level)} {text}")
        elif btype == "bulleted_list_item":
            lines.append(f"- {_rich_text_to_str(data.get('rich_text', []))}")
        elif btype == "numbered_list_item":
            lines.append(f"1. {_rich_text_to_str(data.get('rich_text', []))}")
        elif btype == "to_do":
            checked = "x" if data.get("checked") else " "
            lines.append(f"- [{checked}] {_rich_text_to_str(data.get('rich_text', []))}")
        elif btype == "toggle":
            lines.append(f"**{_rich_text_to_str(data.get('rich_text', []))}**")
        elif btype == "code":
            lang = data.get("language", "")
            lines.append(f"```{lang}")
            lines.append(_rich_text_to_str(data.get("rich_text", [])))
            lines.append("```")
        elif btype == "quote":
            lines.append(f"> {_rich_text_to_str(data.get('rich_text', []))}")
        elif btype == "divider":
            lines.append("---")
        elif btype == "callout":
            lines.append(f"> {_rich_text_to_str(data.get('rich_text', []))}")
        elif btype == "child_page":
            lines.append(f"[Sub-page: {data.get('title', '')}]")
        elif btype == "table":
            pass  # Skip complex tables for now
        else:
            text = _rich_text_to_str(data.get("rich_text", []))
            if text:
                lines.append(text)

    return "\n\n".join(line for line in lines if line)


async def _query_database(db_id: str, status_filter: list[str]) -> list[dict]:
    """Query Notion database for active epics."""
    pages = []
    start_cursor = None

    while True:
        body = {
            "filter": {
                "property": "Status",
                "select": {"is_not_empty": True},
            },
            "sorts": [{"property": "Order", "direction": "ascending"}],
            "page_size": 100,
        }

        # Filter by status
        if status_filter:
            body["filter"] = {
                "or": [
                    {"property": "Status", "status": {"equals": s}}
                    for s in status_filter
                ]
            }

        if start_cursor:
            body["start_cursor"] = start_cursor

        r = await resilient_request(
            "POST", f"{NOTION_API}/databases/{db_id}/query",
            headers=_headers(), json=body,
        )
        data = r.json()

        if r.status_code != 200:
            raise RuntimeError(f"Notion query failed: {data}")

        pages.extend(data.get("results", []))

        if data.get("has_more") and data.get("next_cursor"):
            start_cursor = data["next_cursor"]
        else:
            break

    return pages


async def _get_page_blocks(page_id: str) -> list[dict]:
    """Get all blocks (content) for a page."""
    blocks = []
    start_cursor = None

    while True:
        params = {"page_size": 100}
        if start_cursor:
            params["start_cursor"] = start_cursor

        r = await resilient_request(
            "GET", f"{NOTION_API}/blocks/{page_id}/children",
            headers=_headers(), params=params,
        )
        data = r.json()

        if r.status_code != 200:
            return blocks

        blocks.extend(data.get("results", []))

        if data.get("has_more") and data.get("next_cursor"):
            start_cursor = data["next_cursor"]
        else:
            break

    return blocks


def _slugify(title: str) -> str:
    """Convert title to kebab-case slug for notion_page_id fallback."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-')


async def fetch_and_store_epics(
    db: AsyncSession, week_id, monday: date, sunday: date
) -> tuple[int, int, list[str]]:
    """Fetch epics from Notion API and store in DB."""
    warnings = []

    db_url = await get_config_value(db, "epics", "notion_database_url", "")
    active_statuses = await get_config_value(db, "epics", "active_statuses", [])

    if not settings.notion_api_key:
        warnings.append("NOTION_API_KEY not set")
        return 0, 0, warnings

    db_id = _extract_db_id(db_url)

    # Query database
    pages = await _query_database(db_id, active_statuses)

    # Delete existing epics for this week
    await db.execute(delete(Epic).where(Epic.week_id == week_id))

    epic_count = 0
    sub_page_count = 0

    for page in pages:
        props = page.get("properties", {})
        page_id = page.get("id", "")

        title = _extract_property(props, "Epic") or _extract_property(props, "Name") or "Untitled"
        status = _extract_property(props, "Status") or ""
        team = _extract_property(props, "Team") or []
        if isinstance(team, str):
            team = [team]
        pm_lead = _extract_property(props, "PM / Lead")
        if isinstance(pm_lead, list):
            pm_lead = ", ".join(pm_lead) if pm_lead else None
        sort_order = _extract_property(props, "Order")

        dates = {}
        for date_prop, key in [
            ("Date", "start"), ("Deadline for release", "deadline"),
            ("Date -- In development", "dev_start"), ("Date -- In exploration", "exploration_start"),
            ("Date -- Design", "design_start"), ("Date -- In UAT", "uat_start"),
            ("Date -- Done", "done_start"),
        ]:
            val = _extract_property(props, date_prop)
            if val:
                dates[key] = val

        # Fetch page content
        try:
            blocks = await _get_page_blocks(page_id)
            content = _blocks_to_markdown(blocks)
        except Exception as e:
            content = ""
            warnings.append(f"Failed to fetch content for {title}: {e}")

        epic = Epic(
            week_id=week_id,
            notion_page_id=page_id,
            title=title,
            status=status,
            team=team,
            pm_lead=pm_lead,
            sort_order=int(sort_order) if sort_order else None,
            dates=dates if dates else None,
            content=content,
            properties={k: str(v) for k, v in props.items() if v},
        )
        db.add(epic)
        await db.flush()
        epic_count += 1

        # Fetch sub-pages (one level deep)
        for block in blocks:
            if block.get("type") == "child_page":
                sub_id = block.get("id", "")
                sub_title = block.get("child_page", {}).get("title", "")
                try:
                    sub_blocks = await _get_page_blocks(sub_id)
                    sub_content = _blocks_to_markdown(sub_blocks)
                except Exception as e:
                    sub_content = ""
                    warnings.append(f"Failed to fetch sub-page {sub_title}: {e}")

                sub_page = EpicSubPage(
                    epic_id=epic.id,
                    notion_page_id=sub_id,
                    title=sub_title,
                    content=sub_content,
                )
                db.add(sub_page)
                sub_page_count += 1

    await db.flush()
    return epic_count, sub_page_count, warnings
