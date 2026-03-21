"""Template rendering helpers for the /formatted endpoints."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jinja2 import Environment

LOCAL_TZ = ZoneInfo("America/New_York")


def format_slack_time(slack_ts: str) -> str:
    """Convert slack_ts like '1774272002.595769' to 'HH:MM' in local tz."""
    try:
        dt = datetime.fromtimestamp(float(slack_ts), tz=timezone.utc).astimezone(LOCAL_TZ)
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def ts_digits(slack_ts: str) -> str:
    """Strip the dot from slack_ts (used for Slack deep links and old rufo format)."""
    return (slack_ts or "").replace(".", "")


def day_from_ts(slack_ts: str) -> str:
    """Get YYYY-MM-DD from slack_ts in local tz."""
    try:
        dt = datetime.fromtimestamp(float(slack_ts), tz=timezone.utc).astimezone(LOCAL_TZ)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def day_name(date_or_str) -> str:
    """Get full weekday name (Monday, Tuesday, ...) from YYYY-MM-DD or date."""
    try:
        if isinstance(date_or_str, str):
            dt = datetime.strptime(date_or_str, "%Y-%m-%d")
        else:
            dt = date_or_str
        return dt.strftime("%A")
    except (ValueError, TypeError):
        return ""


def slugify(text: str) -> str:
    """Kebab-case slug for filenames."""
    slug = (text or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "untitled"


def format_datetime(dt) -> str:
    """Format a datetime to 'YYYY-MM-DD HH:MM'."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return dt[:16].replace("T", " ") if len(dt) >= 16 else dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def build_jinja_env() -> Environment:
    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    env.filters["slack_time"] = format_slack_time
    env.filters["ts_digits"] = ts_digits
    env.filters["day_of_ts"] = day_from_ts
    env.filters["day_name"] = day_name
    env.filters["slugify"] = slugify
    env.filters["dt"] = format_datetime
    return env
