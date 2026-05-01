from datetime import date, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import resilient_request
from app.models.meeting import Meeting, MeetingAttendee
from app.services.config_service import get_config_value
from app.services.people_service import resolve_person

FIREFLIES_API = "https://api.fireflies.ai/graphql"

GRAPHQL_QUERY = """
query Transcripts($fromDate: DateTime, $toDate: DateTime, $participants: [String!], $limit: Int) {
  transcripts(fromDate: $fromDate, toDate: $toDate, participants: $participants, limit: $limit) {
    id title dateString duration
    meeting_attendees { name email }
    meeting_info { silent_meeting summary_status }
    summary { keywords overview short_summary notes action_items }
  }
}
"""


def _is_excluded(meeting: dict, excluded_names: list[str], participant_email: str) -> tuple[bool, str]:
    title = meeting.get("title", "")
    info = meeting.get("meeting_info") or {}
    summary = meeting.get("summary")
    attendees = meeting.get("meeting_attendees") or []

    if info.get("silent_meeting") or info.get("summary_status") == "skipped" or summary is None:
        return True, "silent/skipped"

    t = title.lower()
    patterns = ["1:1", "1on1", " / ", " <> "]
    is_1on1 = any(p in t for p in patterns)
    if is_1on1 and any(name in t for name in excluded_names):
        return True, "1:1 with excluded person"

    if len(attendees) == 2:
        other = [a for a in attendees if (a.get("email") or "") != participant_email]
        if other:
            other_str = ((other[0].get("name") or "") + " " + (other[0].get("email") or "")).lower()
            if any(name in other_str for name in excluded_names):
                return True, "1:1 with excluded person (by attendees)"

    return False, ""


async def fetch_and_store_meets(
    db: AsyncSession, week_id, monday: date, sunday: date
) -> tuple[int, int, list[str]]:
    participant_email = await get_config_value(db, "meets", "participant_email", "")
    excluded_names = await get_config_value(db, "meets", "excluded_names", [])

    from_date = monday.isoformat()
    to_date = (sunday + timedelta(days=1)).isoformat()

    warnings: list[str] = []

    response = await resilient_request(
        "POST", FIREFLIES_API,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.fireflies_api_key}"},
        json={"query": GRAPHQL_QUERY, "variables": {"fromDate": from_date, "toDate": to_date, "participants": [participant_email], "limit": 50}},
    )

    if response.status_code != 200:
        snippet = (response.text or "")[:200]
        warnings.append(f"Fireflies HTTP {response.status_code}: {snippet}")
        return 0, 0, warnings

    try:
        data = response.json()
    except ValueError:
        snippet = (response.text or "")[:200]
        warnings.append(f"Fireflies returned non-JSON body (status {response.status_code}): {snippet}")
        return 0, 0, warnings

    if "errors" in data:
        warnings.append(f"Fireflies API errors: {data['errors']}")
        return 0, 0, warnings

    transcripts = data.get("data", {}).get("transcripts", [])

    included = []
    excluded_count = 0
    seen = set()
    for m in transcripts:
        key = (m.get("title", ""), (m.get("dateString") or "")[:10])
        if key in seen:
            continue
        seen.add(key)
        exc, reason = _is_excluded(m, excluded_names, participant_email)
        if exc:
            excluded_count += 1
        else:
            included.append(m)

    await db.execute(delete(Meeting).where(Meeting.week_id == week_id))

    for m in included:
        summary = m.get("summary") or {}
        keywords = summary.get("keywords", "")
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []

        meeting_date = None
        if m.get("dateString"):
            try:
                meeting_date = datetime.fromisoformat(m["dateString"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        meeting = Meeting(
            week_id=week_id, fireflies_id=m.get("id", ""), title=m.get("title", ""),
            meeting_date=meeting_date, duration=m.get("duration"),
            keywords=keywords, overview=summary.get("overview", ""),
            short_summary=summary.get("short_summary", ""), notes=summary.get("notes", ""),
            action_items=summary.get("action_items", ""),
        )
        db.add(meeting)
        await db.flush()

        for a in m.get("meeting_attendees") or []:
            person = None
            if a.get("email"):
                person = await resolve_person(db, email=a["email"], display_name=a.get("name"), fireflies_name=a.get("name"))
            attendee = MeetingAttendee(
                meeting_id=meeting.id, person_id=person.id if person else None,
                name=a.get("name") or a.get("email") or "Unknown", email=a.get("email"),
            )
            db.add(attendee)

    await db.flush()
    return len(included), excluded_count, warnings
