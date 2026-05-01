import asyncio
import logging
import re
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import resilient_request
from app.models.linear import LinearComment, LinearTicket
from app.models.meeting import MeetingAttendee
from app.models.person import Person
from app.models.slack import SlackMessage
from app.services.config_service import get_config_value, upsert_config
from app.services.people_service import resolve_person

SLACK_API = "https://slack.com/api"

# Matches Slack mentions like <@U09GXJJE0LQ> or <@W012345>
MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)>")


def _extract_mentions(text: str) -> set[str]:
    """Extract Slack user IDs from <@U...> mentions in message text."""
    return set(MENTION_RE.findall(text or ""))


async def _slack(method: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {settings.slack_bot_token}", "Content-Type": "application/json; charset=utf-8"}
    # Methods that require GET with query params (Enterprise Grid is strict about this)
    GET_METHODS = {"conversations.replies", "search.messages", "users.info"}
    if method in GET_METHODS:
        response = await resilient_request("GET", f"{SLACK_API}/{method}", headers=headers, params=params)
    else:
        response = await resilient_request("POST", f"{SLACK_API}/{method}", headers=headers, json=params)
    if response.status_code != 200:
        snippet = (response.text or "")[:200]
        return {"ok": False, "error": f"http_{response.status_code}", "http_status": response.status_code, "body_snippet": snippet}
    try:
        return response.json()
    except ValueError:
        snippet = (response.text or "")[:200]
        return {"ok": False, "error": "non_json_body", "http_status": response.status_code, "body_snippet": snippet}


async def _paginate(method: str, params: dict, key: str, delay: float = 0.3) -> list:
    items, cursor = [], None
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        data = await _slack(method, p)
        if not data.get("ok"):
            break
        items.extend(data.get(key, []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            break
        await asyncio.sleep(delay)
    return items


async def _get_watched_channels(db: AsyncSession) -> dict[str, str]:
    """Read fixed channel map from config. No API calls needed."""
    return await get_config_value(db, "slack", "watched_channels", {})


async def _bootstrap_people(db: AsyncSession) -> None:
    count_result = await db.execute(
        select(func.count()).select_from(Person).where(Person.slack_user_id.isnot(None))
    )
    if count_result.scalar() >= 20:
        return
    members = await _paginate("users.list", {"limit": 200}, "members")
    for u in members:
        if u.get("deleted") or u.get("is_bot"):
            continue
        profile = u.get("profile", {})
        name = profile.get("real_name") or profile.get("display_name") or u["id"]
        email = profile.get("email", "") or None
        await resolve_person(db, email=email, slack_user_id=u["id"], display_name=name)
    await db.flush()


async def _resolve_dms(db: AsyncSession) -> list[dict]:
    dm_cache = await get_config_value(db, "slack", "dm_cache", {})
    watched = await get_config_value(db, "slack", "watched_dm_people", [])
    results = []
    new_entries = False

    for wp in watched:
        if wp in dm_cache:
            cached = dm_cache[wp]
            entries = cached if isinstance(cached, list) else [cached]
            results.extend(entries)
            continue

        result = await db.execute(
            select(Person).where(func.lower(Person.display_name).like(f"%{wp.lower().split()[0]}%"))
        )
        person = result.scalar_one_or_none()
        if not person or not person.slack_user_id:
            continue

        data = await _slack("conversations.open", {"users": person.slack_user_id})
        if data.get("ok"):
            dm_id = data.get("channel", {}).get("id")
            if dm_id:
                slug = wp.lower().strip().replace(" ", "-")
                entry = {"id": dm_id, "filename": f"dm-{slug}", "type": "im"}
                results.append(entry)
                dm_cache[wp] = entry
                new_entries = True

    if new_entries:
        await upsert_config(db, "slack", "dm_cache", dm_cache)

    return results


def _has_rich_content(msg: dict) -> bool:
    return bool(msg.get("attachments") or msg.get("blocks") or msg.get("files"))


def _looks_like_placeholder(person: Person) -> bool:
    """Person needs enrichment if display_name is still a raw Slack ID or email is null."""
    if not person.email:
        return True
    dn = person.display_name or ""
    if dn.startswith("U0") or dn.startswith("W0") or dn == person.slack_user_id:
        return True
    return False


async def _enrich_people(db: AsyncSession, slack_user_ids: set[str]) -> int:
    """Backfill placeholder persons by calling users.info for each unknown Slack ID.

    For every encountered Slack user ID (message authors, thread participants, @mentions),
    ensure a Person record exists and has real display_name + email.
    If enrichment reveals an email that matches an existing person (one that lacks slack_user_id),
    merges the two records into the existing person.
    Returns the number of persons that were updated, created, or merged.
    """
    if not slack_user_ids:
        return 0

    updated = 0

    # First: ensure a Person exists for every encountered ID (create placeholder if missing)
    existing_result = await db.execute(
        select(Person).where(Person.slack_user_id.in_(slack_user_ids))
    )
    existing_by_id: dict[str, Person] = {
        p.slack_user_id: p for p in existing_result.scalars().all()
    }

    missing_ids = slack_user_ids - set(existing_by_id.keys())
    for uid in missing_ids:
        person = Person(display_name=uid, slack_user_id=uid)
        db.add(person)
        existing_by_id[uid] = person
    if missing_ids:
        await db.flush()

    # Second: enrich any person that looks like a placeholder via users.info
    for uid, person in list(existing_by_id.items()):
        if not _looks_like_placeholder(person):
            continue
        try:
            data = await _slack("users.info", {"user": uid})
            if not data.get("ok"):
                continue
            user = data.get("user", {}) or {}
            profile = user.get("profile", {}) or {}
            real_name = profile.get("real_name") or profile.get("display_name") or ""
            email = (profile.get("email") or "").strip() or None

            merged = False
            if email and not person.email:
                # Check if another person already has this email
                other_result = await db.execute(
                    select(Person).where(Person.email == email, Person.id != person.id)
                )
                other = other_result.scalar_one_or_none()
                if other:
                    # Merge: transfer slack_user_id to the existing email-based record.
                    # Clear from placeholder first to avoid UNIQUE violation.
                    placeholder_slack_id = person.slack_user_id
                    person.slack_user_id = None
                    await db.flush()

                    if not other.slack_user_id:
                        other.slack_user_id = placeholder_slack_id
                    if not other.display_name or other.display_name == placeholder_slack_id:
                        if real_name:
                            other.display_name = real_name

                    # Reassign all FK references from the placeholder to the merged record
                    placeholder_id = person.id
                    other_id = other.id
                    await db.flush()
                    await db.execute(
                        update(SlackMessage).where(SlackMessage.person_id == placeholder_id).values(person_id=other_id)
                    )
                    await db.execute(
                        update(LinearTicket).where(LinearTicket.person_id == placeholder_id).values(person_id=other_id)
                    )
                    await db.execute(
                        update(LinearComment).where(LinearComment.person_id == placeholder_id).values(person_id=other_id)
                    )
                    await db.execute(
                        update(MeetingAttendee).where(MeetingAttendee.person_id == placeholder_id).values(person_id=other_id)
                    )
                    await db.delete(person)
                    await db.flush()
                    merged = True
                    updated += 1
                else:
                    person.email = email
                    if real_name:
                        person.display_name = real_name
                    updated += 1
                    continue

            if merged:
                continue

            if real_name and (not person.display_name or person.display_name == uid or person.display_name.startswith(("U0", "W0"))):
                person.display_name = real_name
                updated += 1
        except Exception:
            # Skip failed lookups; don't fail the whole fetch
            continue

    if updated:
        await db.flush()
    return updated


async def fetch_and_store_slack(
    db: AsyncSession, week_id, monday: date, sunday: date
) -> tuple[int, int, list[str]]:
    warnings = []
    oldest = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc).timestamp()
    # End of Sunday 23:59:59 UTC — exclusive upper bound
    latest = datetime(sunday.year, sunday.month, sunday.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()

    await _bootstrap_people(db)
    channel_map = await _get_watched_channels(db)
    dm_list = await _resolve_dms(db)

    await db.execute(delete(SlackMessage).where(SlackMessage.week_id == week_id))

    total_msgs = 0
    total_threads = 0
    seen_ts = set()  # Deduplicate thread_broadcast messages across channel history + replies
    encountered_user_ids: set[str] = set()  # Collect all Slack user IDs (authors + mentions) for post-fetch enrichment

    async def _fetch_channel(ch_name: str, ch_id: str, is_dm: bool):
        nonlocal total_msgs, total_threads
        try:
            msgs = await _paginate(
                "conversations.history",
                {"channel": ch_id, "oldest": str(oldest), "latest": str(latest), "limit": 200, "inclusive": False},
                "messages",
            )
            msgs.reverse()

            for msg in msgs:
                ts_key = (ch_id, msg.get("ts", ""))
                if ts_key in seen_ts:
                    continue
                seen_ts.add(ts_key)
                user_id = msg.get("user", "")
                person = None
                if user_id:
                    encountered_user_ids.add(user_id)
                    person = await resolve_person(db, slack_user_id=user_id, display_name=user_id)

                # Collect mentioned user IDs from message text for post-fetch enrichment
                encountered_user_ids.update(_extract_mentions(msg.get("text", "")))

                slack_msg = SlackMessage(
                    week_id=week_id, person_id=person.id if person else None,
                    channel=ch_name, channel_id=ch_id, content=msg.get("text", ""),
                    slack_ts=msg.get("ts", ""), thread_ts=None, is_dm=is_dm,
                    is_thread_reply=False, reactions=msg.get("reactions"),
                    raw=msg if _has_rich_content(msg) else None,
                )
                db.add(slack_msg)
                total_msgs += 1

                if msg.get("reply_count", 0) > 0 and msg.get("ts"):
                    data = await _slack("conversations.replies", {"channel": ch_id, "ts": msg["ts"], "limit": 200})
                    if data.get("ok"):
                        replies = data.get("messages", [])[1:]
                        for reply in replies:
                            reply_key = (ch_id, reply.get("ts", ""))
                            if reply_key in seen_ts:
                                continue
                            seen_ts.add(reply_key)
                            r_user = reply.get("user", "")
                            r_person = None
                            if r_user:
                                encountered_user_ids.add(r_user)
                                r_person = await resolve_person(db, slack_user_id=r_user, display_name=r_user)
                            encountered_user_ids.update(_extract_mentions(reply.get("text", "")))
                            reply_msg = SlackMessage(
                                week_id=week_id, person_id=r_person.id if r_person else None,
                                channel=ch_name, channel_id=ch_id, content=reply.get("text", ""),
                                slack_ts=reply.get("ts", ""), thread_ts=msg.get("ts"),
                                is_dm=is_dm, is_thread_reply=True,
                                reactions=reply.get("reactions"),
                                raw=reply if _has_rich_content(reply) else None,
                            )
                            db.add(reply_msg)
                            total_msgs += 1
                        total_threads += 1

            await asyncio.sleep(1)
        except Exception as e:
            warnings.append(f"Failed to fetch {ch_name}: {e}")

    for ch_name, ch_id in sorted(channel_map.items()):
        await _fetch_channel(ch_name, ch_id, is_dm=False)

    for dm in dm_list:
        await _fetch_channel(dm["filename"], dm["id"], is_dm=True)

    await db.flush()

    # Post-fetch: backfill display_name + email for any placeholder persons
    # encountered during this fetch (message authors + @mentions).
    try:
        enriched = await _enrich_people(db, encountered_user_ids)
        if enriched:
            logger.info("Enriched %d people via users.info", enriched)
    except Exception as e:
        warnings.append(f"People enrichment failed: {e}")

    return total_msgs, total_threads, warnings
