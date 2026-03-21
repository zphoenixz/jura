"""Format DB records back into RUFO-compatible markdown files.

Each formatter returns a list of `{title, content, audit}` — one entry per file.
Templates are loaded from the `configs` table (key `formatted_template`) with
sensible defaults baked in so the endpoints work out of the box.

Grouping rules:
- slack: one file per channel/DM/mpim (by `channel`)
- linear: one file per ticket (by `identifier`)
- meets: one file per meeting (titled `{day-name}-{meeting-slug}.md`)
- epics: one file per epic (by slugified title)
"""

from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.templating import (
    build_jinja_env,
    day_from_ts,
    day_name,
    format_datetime,
    format_slack_time,
    slugify,
    ts_digits,
)
from app.models.epic import Epic
from app.models.linear import LinearTicket
from app.models.meeting import Meeting
from app.models.person import Person
from app.models.slack import SlackMessage
from app.services.config_service import get_config_value

_env = build_jinja_env()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(source_ids: list[str], extra: dict | None = None) -> dict:
    return {
        "source_ids": source_ids,
        "record_count": len(source_ids),
        "generated_at": _now_iso(),
        **(extra or {}),
    }


# ─────────────────────── SLACK ───────────────────────

SLACK_DEFAULT_TEMPLATE = """# {{ channel }}
{% if channel_id -%}
<!-- channel_id: {{ channel_id }} -->
{% endif %}
{% for day in days %}

## {{ day.date }} — {{ day.name }}

{% for m in day.messages %}
**{{ m.time }} ({{ m.ts_digits }}) — {{ m.author_name }}{% if m.author_email %} ({{ m.author_email }}){% endif %}**
{{ m.content }}
{% for r in m.replies %}

  > **{{ r.time }} ({{ r.ts_digits }}|{{ m.ts_digits }}) — {{ r.author_name }}{% if r.author_email %} ({{ r.author_email }}){% endif %}** (thread)
{% for line in r.content.split('\n') %}  > {{ line }}
{% endfor %}
{% endfor %}

{% endfor %}
{% endfor %}
"""


async def format_slack_week(db: AsyncSession, week_id: UUID) -> list[dict]:
    stmt = (
        select(SlackMessage, Person)
        .outerjoin(Person, SlackMessage.person_id == Person.id)
        .where(SlackMessage.week_id == week_id)
        .order_by(SlackMessage.slack_ts)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Group by channel
    channels: dict[str, dict] = defaultdict(
        lambda: {"channel_id": None, "is_dm": False, "items": []}
    )
    for msg, person in rows:
        ch = channels[msg.channel]
        ch["channel_id"] = msg.channel_id
        ch["is_dm"] = msg.is_dm
        ch["items"].append(
            {
                "msg": msg,
                "author_name": person.display_name if person else "Unknown",
                "author_email": (person.email if person else None),
            }
        )

    template_str = await get_config_value(db, "slack", "formatted_template", None)
    template = _env.from_string(template_str or SLACK_DEFAULT_TEMPLATE)

    files = []
    for channel_name, ch_data in channels.items():
        # Separate parents from replies and group replies by parent_ts
        parents: dict[str, dict] = {}
        replies_by_parent: dict[str, list[dict]] = defaultdict(list)
        for item in ch_data["items"]:
            msg = item["msg"]
            if msg.is_thread_reply and msg.thread_ts:
                replies_by_parent[msg.thread_ts].append(item)
            else:
                parents[msg.slack_ts] = item

        # Build day-grouped structure
        days_map: dict[str, list[dict]] = defaultdict(list)
        source_ids: list[str] = []
        for ts in sorted(parents.keys()):
            item = parents[ts]
            msg = item["msg"]
            day = day_from_ts(msg.slack_ts)
            source_ids.append(str(msg.id))

            reply_list = []
            for r_item in sorted(
                replies_by_parent.get(ts, []),
                key=lambda x: x["msg"].slack_ts,
            ):
                r = r_item["msg"]
                source_ids.append(str(r.id))
                reply_list.append(
                    {
                        "slack_ts": r.slack_ts,
                        "ts_digits": ts_digits(r.slack_ts),
                        "time": format_slack_time(r.slack_ts),
                        "author_name": r_item["author_name"],
                        "author_email": r_item["author_email"],
                        "content": r.content or "",
                    }
                )

            days_map[day].append(
                {
                    "slack_ts": msg.slack_ts,
                    "ts_digits": ts_digits(msg.slack_ts),
                    "time": format_slack_time(msg.slack_ts),
                    "author_name": item["author_name"],
                    "author_email": item["author_email"],
                    "content": msg.content or "",
                    "replies": reply_list,
                }
            )

        days = [
            {"date": d, "name": day_name(d), "messages": days_map[d]}
            for d in sorted(days_map.keys())
        ]

        content = template.render(
            channel=channel_name,
            channel_id=ch_data["channel_id"],
            is_dm=ch_data["is_dm"],
            days=days,
        )

        files.append(
            {
                "title": f"{channel_name}.md",
                "content": content.rstrip() + "\n",
                "audit": _audit(
                    source_ids,
                    {"channel_id": ch_data["channel_id"], "is_dm": ch_data["is_dm"]},
                ),
            }
        )

    files.sort(key=lambda f: f["title"])
    return files


# ─────────────────────── LINEAR ───────────────────────

LINEAR_DEFAULT_TEMPLATE = """## {{ ticket_type }} {{ identifier }} — {{ title }}

# properties: {{ status_upper }}, {{ priority_label_upper }}, {{ assignee_email }}, {{ points }}pts
# labels: {{ labels_str }}
{% if ticket_type == 'PARENT' and child_identifiers %}
# subissues: {{ child_identifiers|join(', ') }}
{% elif ticket_type == 'CHILD' and parent_identifier %}
# parent: {{ parent_identifier }}
{% endif %}

# description
{{ description or '(no description)' }}

# activity
{% if comments %}
{% for c in comments %}
**{{ c.author_name }} ({{ c.linear_created_at|dt }}): {{ c.body }}
{% endfor %}
{% else %}
(no comments)
{% endif %}

# resources
{% for url in resource_urls %}
- {{ url }}
{% endfor %}

# last updated
{{ linear_updated_at|dt }}
"""


async def format_linear_week(db: AsyncSession, week_id: UUID) -> list[dict]:
    stmt = (
        select(LinearTicket)
        .where(LinearTicket.week_id == week_id)
        .options(selectinload(LinearTicket.comments))
        .order_by(LinearTicket.priority, LinearTicket.identifier)
    )
    result = await db.execute(stmt)
    tickets = list(result.scalars().unique().all())

    # Fetch assignee persons in one query
    assignee_ids = {t.person_id for t in tickets if t.person_id}
    people_by_id: dict = {}
    if assignee_ids:
        r = await db.execute(select(Person).where(Person.id.in_(assignee_ids)))
        people_by_id = {p.id: p for p in r.scalars().all()}

    template_str = await get_config_value(db, "linear", "formatted_template", None)
    template = _env.from_string(template_str or LINEAR_DEFAULT_TEMPLATE)

    files = []
    for t in tickets:
        assignee = people_by_id.get(t.person_id) if t.person_id else None
        assignee_email = (assignee.email if assignee else None) or "unassigned"

        # Ticket type
        children = t.child_identifiers or []
        if children:
            ticket_type = "PARENT"
        elif t.parent_identifier:
            ticket_type = "CHILD"
        else:
            ticket_type = "SINGLE"

        labels = t.labels or []
        labels_str = ", ".join(labels) if labels else "none"

        # Extract URLs from description, comments, attachments
        import re as _re

        url_re = _re.compile(r"https?://[^\s\)\]>]+")
        urls: list[str] = []

        def _add_urls(text: str):
            if text:
                urls.extend(url_re.findall(text))

        _add_urls(t.description or "")
        for c in t.comments:
            _add_urls(c.body or "")
        for att in t.attachments or []:
            u = att.get("url") if isinstance(att, dict) else None
            if u:
                urls.append(u)

        # Dedup, sort: github PRs first, then others, then linear URL last
        unique_urls = []
        seen = set()
        for u in urls:
            if u and u not in seen and u != t.url:
                seen.add(u)
                unique_urls.append(u)
        gh_prs = sorted([u for u in unique_urls if _re.search(r"github\.com/.*/pull/\d+", u)])
        others = sorted([u for u in unique_urls if u not in gh_prs])
        resource_urls = gh_prs + others
        if t.url:
            resource_urls.append(t.url)

        # Sort comments newest-first
        sorted_comments = sorted(
            t.comments,
            key=lambda c: c.linear_created_at or datetime.min,
            reverse=True,
        )

        ctx = {
            "ticket_type": ticket_type,
            "identifier": t.identifier,
            "title": t.title,
            "status_upper": (t.status or "").upper(),
            "priority_label_upper": (t.priority_label or "").upper(),
            "assignee_email": assignee_email,
            "points": t.points or 0,
            "labels_str": labels_str,
            "child_identifiers": children,
            "parent_identifier": t.parent_identifier,
            "description": t.description,
            "comments": sorted_comments,
            "resource_urls": resource_urls,
            "linear_updated_at": t.linear_updated_at,
        }

        content = template.render(**ctx)
        source_ids = [str(t.id)] + [str(c.id) for c in t.comments]

        files.append(
            {
                "title": f"{t.identifier}.md",
                "content": content.rstrip() + "\n",
                "audit": _audit(
                    source_ids,
                    {
                        "identifier": t.identifier,
                        "status": t.status,
                        "cycle_number": t.cycle_number,
                    },
                ),
            }
        )

    files.sort(key=lambda f: f["title"])
    return files


# ─────────────────────── MEETS ───────────────────────

MEETS_DEFAULT_TEMPLATE = """## title: {{ title }} -- date: {{ date }}

**Keywords:** {{ keywords_str }}

**Summary:**
{{ notes or overview or short_summary or '(no summary)' }}

**Action Items:**

{{ action_items or '(none)' }}
"""


async def format_meets_week(db: AsyncSession, week_id: UUID) -> list[dict]:
    stmt = (
        select(Meeting)
        .where(Meeting.week_id == week_id)
        .options(selectinload(Meeting.attendees))
        .order_by(Meeting.meeting_date)
    )
    result = await db.execute(stmt)
    meetings = list(result.scalars().all())

    template_str = await get_config_value(db, "meets", "formatted_template", None)
    template = _env.from_string(template_str or MEETS_DEFAULT_TEMPLATE)

    files = []
    for m in meetings:
        keywords = m.keywords or []
        if isinstance(keywords, str):
            keywords_str = keywords
        elif isinstance(keywords, list):
            keywords_str = ", ".join(keywords)
        else:
            keywords_str = ""

        date_str = ""
        dow = "unknown"
        if m.meeting_date:
            dow = m.meeting_date.strftime("%A").lower()
            date_str = m.meeting_date.strftime("%Y-%m-%d")

        ctx = {
            "title": m.title,
            "date": date_str,
            "keywords_str": keywords_str,
            "notes": m.notes,
            "overview": m.overview,
            "short_summary": m.short_summary,
            "action_items": m.action_items,
        }
        content = template.render(**ctx)

        title_slug = slugify(m.title)
        file_title = f"{dow}-{title_slug}.md"

        files.append(
            {
                "title": file_title,
                "content": content.rstrip() + "\n",
                "audit": _audit(
                    [str(m.id)],
                    {"fireflies_id": m.fireflies_id, "meeting_date": date_str},
                ),
            }
        )

    # Sort by date then title
    files.sort(key=lambda f: f["title"])
    return files


# ─────────────────────── EPICS ───────────────────────

EPICS_DEFAULT_TEMPLATE = """# {{ title }}

## Properties

- **Status:** {{ status }}
- **Team:** {{ team_str }}
- **PM / Lead:** {{ pm_lead or '-' }}
- **Order:** {{ sort_order if sort_order is not none else '-' }}
{% if dates %}
{% if dates.start %}- **Date:** {{ dates.start }}{% if dates.end %} → {{ dates.end }}{% endif %}
{% endif %}
{% if dates.deadline %}- **Deadline:** {{ dates.deadline }}
{% endif %}
{% if dates.dev_start %}- **In Development Since:** {{ dates.dev_start }}
{% endif %}
{% if dates.exploration_start %}- **In Exploration Since:** {{ dates.exploration_start }}
{% endif %}
{% if dates.design_start %}- **In Design Since:** {{ dates.design_start }}
{% endif %}
{% if dates.uat_start %}- **In UAT Since:** {{ dates.uat_start }}
{% endif %}
{% endif %}

---

## Content

{{ content or '(empty)' }}
{% if sub_pages %}

---

## Sub-Pages
{% for sp in sub_pages %}

### {{ sp.title }}

{{ sp.content or '(empty)' }}
{% if not loop.last %}

---
{% endif %}
{% endfor %}
{% endif %}
"""


async def format_epics_week(db: AsyncSession, week_id: UUID) -> list[dict]:
    stmt = (
        select(Epic)
        .where(Epic.week_id == week_id)
        .options(selectinload(Epic.sub_pages))
        .order_by(Epic.sort_order.asc().nullslast(), Epic.title)
    )
    result = await db.execute(stmt)
    epics = list(result.scalars().all())

    template_str = await get_config_value(db, "epics", "formatted_template", None)
    template = _env.from_string(template_str or EPICS_DEFAULT_TEMPLATE)

    files = []
    for e in epics:
        team = e.team or []
        if isinstance(team, list):
            team_str = ", ".join(team) if team else "-"
        else:
            team_str = str(team)

        ctx = {
            "title": e.title,
            "status": e.status,
            "team_str": team_str,
            "pm_lead": e.pm_lead,
            "sort_order": e.sort_order,
            "dates": e.dates or {},
            "content": e.content,
            "sub_pages": e.sub_pages,
        }
        content = template.render(**ctx)

        source_ids = [str(e.id)] + [str(sp.id) for sp in e.sub_pages]
        files.append(
            {
                "title": f"{slugify(e.title)}.md",
                "content": content.rstrip() + "\n",
                "audit": _audit(
                    source_ids,
                    {
                        "notion_page_id": e.notion_page_id,
                        "status": e.status,
                        "sub_pages": len(e.sub_pages),
                    },
                ),
            }
        )

    files.sort(key=lambda f: f["title"])
    return files
