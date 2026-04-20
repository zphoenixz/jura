import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.http_client import resilient_request
from app.models.linear import LinearComment, LinearTicket
from app.models.meeting import MeetingAttendee
from app.models.person import Person
from app.models.slack import SlackMessage
from app.services.config_service import get_config_value
from app.services.people_service import resolve_person

LINEAR_API = "https://api.linear.app/graphql"

PRIORITY_MAP = {0: "No Priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
STATUS_TYPE_TO_CATEGORY = {
    "unstarted": "todo",
    "backlog": "todo",
    "triage": "todo",
    "started": "in_progress",
    "completed": "done",
    "canceled": "discarded",
}

QUERY_TEAM = "query Team($name: String!) { teams(filter: { name: { eq: $name } }) { nodes { id name } } }"
QUERY_ACTIVE_CYCLE = "query ActiveCycle($teamId: String!) { team(id: $teamId) { activeCycle { id number name startsAt endsAt } } }"
QUERY_CYCLES_BY_DATE = """
query CyclesByDate($teamId: String!, $endsBefore: DateTimeOrDuration!, $startsAfter: DateTimeOrDuration!) {
  team(id: $teamId) {
    cycles(filter: { startsAt: { lt: $endsBefore }, endsAt: { gt: $startsAfter } }) {
      nodes { id number name startsAt endsAt }
    }
  }
}
"""
QUERY_CYCLE_ISSUES = """
query CycleIssues($cycleId: String!, $after: String) {
  cycle(id: $cycleId) {
    issues(first: 250, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id identifier title description priority estimate url updatedAt createdAt
        state { name type }
        assignee { id name email }
        labels { nodes { name } }
        parent { identifier }
        children { nodes { identifier } }
        comments(first: 10) { nodes { id body createdAt user { id name email } } }
        attachments { nodes { url title } }
      }
    }
  }
}
"""

QUERY_ISSUES_BY_NUMBER = """
query IssuesByNumber($teamId: ID!, $numbers: [Float!]!, $after: String) {
  issues(filter: { team: { id: { eq: $teamId } }, number: { in: $numbers } }, first: 250, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id identifier title description priority estimate url updatedAt createdAt
      state { name type }
      assignee { id name email }
      labels { nodes { name } }
      parent { identifier }
      children { nodes { identifier } }
    }
  }
}
"""

QUERY_USER = (
    "query User($id: String!) { user(id: $id) { id name email displayName active } }"
)


async def graphql(query: str, variables: dict) -> dict:
    response = await resilient_request(
        "POST",
        LINEAR_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": settings.linear_api_key,
        },
        json={"query": query, "variables": variables},
    )
    data = response.json()
    if "errors" in data:
        raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
    return data.get("data", {})


async def _find_cycle(team_id: str, monday: date, sunday: date) -> dict | None:
    from datetime import timedelta

    # Only use active cycle if the requested week contains today
    today = date.today()
    if monday <= today <= sunday:
        data = await graphql(QUERY_ACTIVE_CYCLE, {"teamId": team_id})
        cycle = data.get("team", {}).get("activeCycle")
        if cycle:
            return cycle
    # Historical or fallback: find cycle by date range
    data = await graphql(
        QUERY_CYCLES_BY_DATE,
        {
            "teamId": team_id,
            "startsAfter": monday.isoformat(),
            "endsBefore": sunday.isoformat(),
        },
    )
    cycles = data.get("team", {}).get("cycles", {}).get("nodes", [])
    return cycles[0] if cycles else None


async def _fetch_all_issues(cycle_id: str) -> list[dict]:
    all_issues = []
    after = None
    while True:
        variables = {"cycleId": cycle_id}
        if after:
            variables["after"] = after
        data = await graphql(QUERY_CYCLE_ISSUES, variables)
        issues_data = data.get("cycle", {}).get("issues", {})
        all_issues.extend(issues_data.get("nodes", []))
        page_info = issues_data.get("pageInfo", {})
        if page_info.get("hasNextPage") and page_info.get("endCursor"):
            after = page_info["endCursor"]
        else:
            break
    return all_issues


def _parse_issue_number(identifier: str) -> int | None:
    """Extract the numeric part from an identifier like 'ABC-2114' → 2114."""
    import re

    m = re.search(r"-(\d+)$", identifier)
    return int(m.group(1)) if m else None


async def _fetch_issues_by_numbers(team_id: str, identifiers: list[str]) -> list[dict]:
    """Fetch issues by team + number in batches of 50."""
    numbers = [
        n for ident in identifiers if (n := _parse_issue_number(ident)) is not None
    ]
    if not numbers:
        return []
    all_issues = []
    for i in range(0, len(numbers), 50):
        batch = numbers[i : i + 50]
        after = None
        while True:
            variables: dict = {"teamId": team_id, "numbers": [float(n) for n in batch]}
            if after:
                variables["after"] = after
            data = await graphql(QUERY_ISSUES_BY_NUMBER, variables)
            issues_data = data.get("issues", {})
            all_issues.extend(issues_data.get("nodes", []))
            page_info = issues_data.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                after = page_info["endCursor"]
            else:
                break
    return all_issues


def _parse_datetime(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, IndexError):
        return None


def _looks_like_linear_placeholder(person: Person) -> bool:
    """Person needs enrichment if email is null or display_name equals linear_user_id."""
    if not person.email:
        return True
    dn = person.display_name or ""
    if dn == person.linear_user_id:
        return True
    return False


async def _enrich_linear_people(db: AsyncSession, linear_user_ids: set[str]) -> int:
    """Backfill placeholder persons by calling Linear's user(id) query.

    Resolves the Person for each Linear user ID (assignees + comment authors),
    ensuring display_name and email are populated. If enrichment finds an existing
    person with the same email, merges the two records.
    Returns the number of persons updated, created, or merged.
    """
    if not linear_user_ids:
        return 0

    result = await db.execute(
        select(Person).where(Person.linear_user_id.in_(linear_user_ids))
    )
    people = list(result.scalars().all())

    updated = 0
    for person in people:
        if not _looks_like_linear_placeholder(person):
            continue
        try:
            data = await graphql(QUERY_USER, {"id": person.linear_user_id})
            user = data.get("user") or {}
            if not user:
                continue
            real_name = user.get("name") or user.get("displayName") or ""
            email = (user.get("email") or "").strip() or None

            merged = False
            if email and not person.email:
                other_result = await db.execute(
                    select(Person).where(Person.email == email, Person.id != person.id)
                )
                other = other_result.scalar_one_or_none()
                if other:
                    # Merge: transfer linear_user_id to the existing email-based record.
                    # Clear from placeholder first to avoid UNIQUE violation.
                    placeholder_linear_id = person.linear_user_id
                    person.linear_user_id = None
                    await db.flush()

                    if not other.linear_user_id:
                        other.linear_user_id = placeholder_linear_id
                    if (
                        not other.display_name
                        or other.display_name == placeholder_linear_id
                    ):
                        if real_name:
                            other.display_name = real_name

                    placeholder_id = person.id
                    other_id = other.id
                    await db.flush()
                    await db.execute(
                        update(SlackMessage)
                        .where(SlackMessage.person_id == placeholder_id)
                        .values(person_id=other_id)
                    )
                    await db.execute(
                        update(LinearTicket)
                        .where(LinearTicket.person_id == placeholder_id)
                        .values(person_id=other_id)
                    )
                    await db.execute(
                        update(LinearComment)
                        .where(LinearComment.person_id == placeholder_id)
                        .values(person_id=other_id)
                    )
                    await db.execute(
                        update(MeetingAttendee)
                        .where(MeetingAttendee.person_id == placeholder_id)
                        .values(person_id=other_id)
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

            if real_name and (
                not person.display_name or person.display_name == person.linear_user_id
            ):
                person.display_name = real_name
                updated += 1
        except Exception:
            continue

    if updated:
        await db.flush()
    return updated


async def fetch_and_store_linear(
    db: AsyncSession, week_id, monday: date, sunday: date
) -> tuple[int, int, int | None, list[str]]:
    warnings = []
    team_name = await get_config_value(db, "linear", "team_name")

    data = await graphql(QUERY_TEAM, {"name": team_name})
    nodes = data.get("teams", {}).get("nodes", [])
    if not nodes:
        warnings.append(f"Team '{team_name}' not found")
        return 0, 0, None, warnings
    team_id = nodes[0]["id"]

    cycle = await _find_cycle(team_id, monday, sunday)
    if not cycle:
        warnings.append("No cycle found for this week")
        return 0, 0, None, warnings

    cycle_number = cycle.get("number")
    cycle_name = cycle.get("name", f"Cycle {cycle_number}")
    issues = await _fetch_all_issues(cycle["id"])

    # Chase direct relatives (parents/children) not in the cycle — 1 hop only
    cycle_identifiers = {issue.get("identifier") for issue in issues}
    missing_identifiers: set[str] = set()
    for issue in issues:
        parent_id = (issue.get("parent") or {}).get("identifier")
        if parent_id and parent_id not in cycle_identifiers:
            missing_identifiers.add(parent_id)
        for child in issue.get("children", {}).get("nodes") or []:
            child_id = child.get("identifier")
            if child_id and child_id not in cycle_identifiers:
                missing_identifiers.add(child_id)

    related_issues: list[dict] = []
    if missing_identifiers:
        try:
            related_issues = await _fetch_issues_by_numbers(
                team_id, list(missing_identifiers)
            )
            logger.info("Fetched %d out-of-cycle relatives", len(related_issues))
        except Exception as e:
            warnings.append(f"Failed to fetch out-of-cycle relatives: {e}")

    await db.execute(delete(LinearTicket).where(LinearTicket.week_id == week_id))

    ticket_count = 0
    comment_count = 0
    encountered_user_ids: set[str] = set()

    for issue in issues:
        state = issue.get("state") or {}
        assignee = issue.get("assignee") or {}
        labels = [
            l.get("name", "") for l in (issue.get("labels", {}).get("nodes") or [])
        ]
        parent = issue.get("parent") or {}
        children = issue.get("children", {}).get("nodes") or []

        # Always resolve person when we have any identifier — email, id, or name
        person = None
        if assignee.get("id") or assignee.get("email") or assignee.get("name"):
            uid = assignee.get("id")
            if uid:
                encountered_user_ids.add(uid)
            person = await resolve_person(
                db,
                email=assignee.get("email") or None,
                display_name=assignee.get("name") or uid,
                linear_user_id=uid,
            )

        ticket = LinearTicket(
            week_id=week_id,
            person_id=person.id if person else None,
            linear_id=issue.get("id", ""),
            identifier=issue.get("identifier", ""),
            title=issue.get("title", ""),
            description=issue.get("description"),
            status=state.get("name", "Unknown"),
            status_type=STATUS_TYPE_TO_CATEGORY.get(
                state.get("type", "unstarted"), "todo"
            ),
            priority=issue.get("priority", 0) or 0,
            priority_label=PRIORITY_MAP.get(
                issue.get("priority", 0) or 0, "No Priority"
            ),
            labels=labels if labels else None,
            points=issue.get("estimate"),
            cycle_number=cycle_number,
            cycle_name=cycle_name,
            in_cycle=True,
            parent_identifier=parent.get("identifier"),
            child_identifiers=(
                [c.get("identifier") for c in children] if children else None
            ),
            attachments=[
                {"url": a.get("url"), "title": a.get("title")}
                for a in (issue.get("attachments", {}).get("nodes") or [])
            ],
            url=issue.get("url"),
            linear_created_at=_parse_datetime(issue.get("createdAt")),
            linear_updated_at=_parse_datetime(issue.get("updatedAt")),
        )
        db.add(ticket)
        await db.flush()
        ticket_count += 1

        for c in issue.get("comments", {}).get("nodes") or []:
            user = c.get("user") or {}
            comment_person = None
            if user.get("id") or user.get("email") or user.get("name"):
                uid = user.get("id")
                if uid:
                    encountered_user_ids.add(uid)
                comment_person = await resolve_person(
                    db,
                    email=user.get("email") or None,
                    display_name=user.get("name") or uid,
                    linear_user_id=uid,
                )
            comment = LinearComment(
                ticket_id=ticket.id,
                linear_comment_id=c.get("id"),
                person_id=comment_person.id if comment_person else None,
                author_name=user.get("name", "Unknown"),
                body=c.get("body", ""),
                linear_created_at=_parse_datetime(c.get("createdAt")),
            )
            db.add(comment)
            comment_count += 1

    # Store out-of-cycle relatives (no comments — they're structural bridges)
    for issue in related_issues:
        state = issue.get("state") or {}
        assignee = issue.get("assignee") or {}
        labels = [
            l.get("name", "") for l in (issue.get("labels", {}).get("nodes") or [])
        ]
        parent = issue.get("parent") or {}
        children = issue.get("children", {}).get("nodes") or []

        person = None
        if assignee.get("id") or assignee.get("email") or assignee.get("name"):
            uid = assignee.get("id")
            if uid:
                encountered_user_ids.add(uid)
            person = await resolve_person(
                db,
                email=assignee.get("email") or None,
                display_name=assignee.get("name") or uid,
                linear_user_id=uid,
            )

        ticket = LinearTicket(
            week_id=week_id,
            person_id=person.id if person else None,
            linear_id=issue.get("id", ""),
            identifier=issue.get("identifier", ""),
            title=issue.get("title", ""),
            description=issue.get("description"),
            status=state.get("name", "Unknown"),
            status_type=STATUS_TYPE_TO_CATEGORY.get(
                state.get("type", "unstarted"), "todo"
            ),
            priority=issue.get("priority", 0) or 0,
            priority_label=PRIORITY_MAP.get(
                issue.get("priority", 0) or 0, "No Priority"
            ),
            labels=labels if labels else None,
            points=issue.get("estimate"),
            cycle_number=cycle_number,
            cycle_name=cycle_name,
            in_cycle=False,
            parent_identifier=parent.get("identifier"),
            child_identifiers=(
                [c.get("identifier") for c in children] if children else None
            ),
            url=issue.get("url"),
            linear_created_at=_parse_datetime(issue.get("createdAt")),
            linear_updated_at=_parse_datetime(issue.get("updatedAt")),
        )
        db.add(ticket)
        ticket_count += 1

    await db.flush()

    # Post-fetch: backfill email/display_name for any placeholder persons
    try:
        enriched = await _enrich_linear_people(db, encountered_user_ids)
        if enriched:
            logger.info("Enriched %d people via Linear user query", enriched)
    except Exception as e:
        warnings.append(f"People enrichment failed: {e}")

    return ticket_count, comment_count, cycle_number, warnings
