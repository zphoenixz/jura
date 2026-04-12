"""Mention interpolation helpers for Slack and Linear content.

Replaces raw user ID mentions (e.g. `<@U08DS6QBBA8>`) and username mentions
(e.g. `@john.doe`) with human-readable `@Display Name (email)` labels
at GET time — the raw data stays stored as-is.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person

# Slack: <@U08DS6QBBA8> or <@W01234>
SLACK_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)>")

# Linear: @<email-prefix> where prefix can include . _ -
LINEAR_MENTION_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9._-]*)")


def _label_for(person: Person) -> str:
    """Build a human-readable label for a person."""
    name = (person.display_name or "").strip()
    email = person.email
    # Avoid redundant "email (email)" if display_name is just the email
    if email and name and name != email:
        return f"{name} ({email})"
    if email:
        return email
    return name


async def build_slack_mention_map(db: AsyncSession, texts: list[str]) -> dict[str, str]:
    """Extract all <@U...> mentions from a list of texts and resolve to labels.

    Returns a dict mapping slack_user_id -> 'Display Name (email)'.
    Unresolved IDs are omitted from the result.
    """
    ids: set[str] = set()
    for text in texts:
        if text:
            ids.update(SLACK_MENTION_RE.findall(text))
    if not ids:
        return {}
    result = await db.execute(select(Person).where(Person.slack_user_id.in_(ids)))
    return {
        p.slack_user_id: _label_for(p)
        for p in result.scalars().all()
        if p.slack_user_id
    }


async def build_linear_mention_map(
    db: AsyncSession, texts: list[str]
) -> dict[str, str]:
    """Extract all @username mentions from Linear content and resolve to labels.

    Linear uses `@<email-prefix>` (e.g. @jane.doe for jane.doe@example.com).
    Returns a dict mapping username -> 'Display Name (email)'.
    """
    names: set[str] = set()
    for text in texts:
        if text:
            names.update(LINEAR_MENTION_RE.findall(text))
    if not names:
        return {}

    # Load all people with emails; match by email prefix (case-insensitive)
    result = await db.execute(select(Person).where(Person.email.isnot(None)))
    people = list(result.scalars().all())

    mapping: dict[str, str] = {}
    for name in names:
        name_lower = name.lower()
        for p in people:
            if not p.email:
                continue
            prefix = p.email.split("@")[0].lower()
            if prefix == name_lower:
                mapping[name] = _label_for(p)
                break
    return mapping


def replace_slack_mentions(text: str | None, mapping: dict[str, str]) -> str:
    """Replace <@U...> mentions in text with @Display Name (email)."""
    if not text:
        return text or ""

    def _sub(m: re.Match) -> str:
        uid = m.group(1)
        label = mapping.get(uid)
        return f"@{label}" if label else m.group(0)

    return SLACK_MENTION_RE.sub(_sub, text)


def replace_linear_mentions(text: str | None, mapping: dict[str, str]) -> str:
    """Replace @username mentions in Linear content with @Display Name (email).

    Only replaces mentions that resolve to a known person; unknown @words are left as-is.
    """
    if not text:
        return text or ""

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        label = mapping.get(name)
        return f"@{label}" if label else m.group(0)

    return LINEAR_MENTION_RE.sub(_sub, text)
