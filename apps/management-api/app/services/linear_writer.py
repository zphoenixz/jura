"""Linear ticket mutation service: compute tree diffs, execute via GraphQL."""

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week_utils import resolve_week
from app.models.linear import LinearTicket
from app.models.person import Person
from app.schemas.linear import LinearMutationOp, LinearTicketCreate, LinearTicketPatch
from app.services.config_service import get_config_value
from app.services.linear_fetcher import (
    graphql, QUERY_ACTIVE_CYCLE, STATUS_TYPE_TO_CATEGORY, PRIORITY_MAP, _parse_datetime,
)
from app.services.linear_lookups import (
    get_default_state_id,
    get_team_id,
    resolve_label_ids,
    resolve_state_id,
)
from app.services.week_service import get_or_create_week

MUTATION_ISSUE_UPDATE = """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue { id identifier title }
  }
}
"""

MUTATION_ISSUE_CREATE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
  }
}
"""

QUERY_ISSUE_BY_IDENTIFIER = """
query IssueByIdentifier($identifier: String!) {
  issueSearch(query: $identifier, first: 1) {
    nodes { id identifier }
  }
}
"""

# Concurrency limit for parallel Linear API calls
_semaphore = asyncio.Semaphore(5)


async def _resolve_linear_id(db: AsyncSession, week_id, identifier: str) -> str:
    """Resolve a ticket identifier (e.g. TEAM-1234) to its Linear internal UUID.

    First checks local DB for the current week. Falls back to Linear API search.
    """
    result = await db.execute(
        select(LinearTicket.linear_id).where(
            LinearTicket.week_id == week_id,
            LinearTicket.identifier == identifier,
        )
    )
    linear_id = result.scalar_one_or_none()
    if linear_id:
        return linear_id

    # Cross-cycle fallback: query Linear directly
    data = await graphql(QUERY_ISSUE_BY_IDENTIFIER, {"identifier": identifier})
    nodes = data.get("issueSearch", {}).get("nodes", [])
    if nodes:
        return nodes[0]["id"]

    raise ValueError(f"Ticket {identifier} not found in DB or Linear")


async def _get_ticket(db: AsyncSession, week_id, identifier: str) -> LinearTicket | None:
    """Get a ticket from the current week's DB by identifier."""
    result = await db.execute(
        select(LinearTicket).where(
            LinearTicket.week_id == week_id,
            LinearTicket.identifier == identifier,
        )
    )
    return result.scalar_one_or_none()


def _detect_cycle(tickets_by_id: dict[str, dict], node_id: str, new_parent_id: str | None) -> bool:
    """Check if setting node's parent to new_parent would create a cycle.

    Walk from new_parent up to root. If we encounter node_id, it's a cycle.
    """
    if new_parent_id is None:
        return False
    if new_parent_id == node_id:
        return True

    visited = {node_id}
    current = new_parent_id
    depth = 0
    while current and depth < 50:
        if current in visited:
            return True
        visited.add(current)
        ticket = tickets_by_id.get(current)
        if not ticket:
            break
        current = ticket.get("parent_identifier")
        depth += 1
    return False


async def _execute_mutation(op_desc: str, query: str, variables: dict) -> LinearMutationOp:
    """Execute a single Linear GraphQL mutation with concurrency control."""
    async with _semaphore:
        try:
            data = await graphql(query, variables)
            for key in ("issueUpdate", "issueCreate"):
                result = data.get(key)
                if result:
                    if result.get("success"):
                        issue = result.get("issue", {})
                        return LinearMutationOp(
                            identifier=issue.get("identifier", ""),
                            op=op_desc,
                            value=str(variables.get("input", {}).get("parentId", "")),
                            status="ok",
                        )
                    else:
                        return LinearMutationOp(
                            identifier=variables.get("id", ""),
                            op=op_desc,
                            status="failed",
                            error="Linear returned success=false",
                        )
            return LinearMutationOp(
                identifier=variables.get("id", ""),
                op=op_desc,
                status="failed",
                error=f"Unexpected response shape",
            )
        except Exception as e:
            return LinearMutationOp(
                identifier=variables.get("id", "unknown"),
                op=op_desc,
                status="failed",
                error=str(e),
            )


async def _resolve_assignee_linear_id(db: AsyncSession, person_uuid: UUID) -> str:
    """Resolve a person UUID to their Linear user ID."""
    result = await db.execute(select(Person).where(Person.id == person_uuid))
    person = result.scalar_one_or_none()
    if not person:
        raise ValueError(f"Person {person_uuid} not found")
    if not person.linear_user_id:
        raise ValueError(f"Person {person.display_name} has no Linear user ID")
    return person.linear_user_id


async def patch_ticket(
    db: AsyncSession, identifier: str, patch: LinearTicketPatch, raw_body: dict
) -> tuple[str, list[LinearMutationOp]]:
    """Apply a patch to a Linear ticket. Returns (target_identifier, list_of_ops)."""
    monday, sunday = resolve_week(None)
    w = await get_or_create_week(db, monday)
    week_id = w.id

    team_name = await get_config_value(db, "linear", "team_name")
    team_id = await get_team_id(team_name)

    # Load all current-week tickets for cycle detection and tree state
    result = await db.execute(
        select(LinearTicket).where(LinearTicket.week_id == week_id)
    )
    all_tickets = list(result.scalars().all())
    tickets_by_id = {
        t.identifier: {
            "linear_id": t.linear_id,
            "identifier": t.identifier,
            "parent_identifier": t.parent_identifier,
            "child_identifiers": t.child_identifiers or [],
        }
        for t in all_tickets
    }

    # Resolve target ticket
    target = tickets_by_id.get(identifier)
    if not target:
        linear_id = await _resolve_linear_id(db, week_id, identifier)
        target = {
            "linear_id": linear_id,
            "identifier": identifier,
            "parent_identifier": None,
            "child_identifiers": [],
        }

    mutations: list[tuple[str, str, dict]] = []  # (op_desc, query, variables)

    # --- Relationship: parent ---
    parent_explicitly_set = "parent" in raw_body
    children_explicitly_set = "children" in raw_body

    if parent_explicitly_set:
        new_parent_id = patch.parent
        old_parent_id = target["parent_identifier"]

        if new_parent_id and _detect_cycle(tickets_by_id, identifier, new_parent_id):
            raise ValueError(f"Setting parent of {identifier} to {new_parent_id} would create a cycle")

        # Set the target's new parent (children stay with the ticket — Linear preserves them)
        parent_input: dict = {}
        if new_parent_id:
            parent_input["parentId"] = await _resolve_linear_id(db, week_id, new_parent_id)
        else:
            parent_input["parentId"] = None
        mutations.append((
            "set_parent",
            MUTATION_ISSUE_UPDATE,
            {"id": target["linear_id"], "input": parent_input},
        ))

    # --- Relationship: children ---
    if children_explicitly_set:
        desired_children = patch.children if patch.children is not None else []
        old_children = set(target["child_identifiers"])
        new_children = set(desired_children)
        old_parent_id = target["parent_identifier"]

        # Children to remove: promote to target's parent
        for child_id in old_children - new_children:
            child_linear_id = await _resolve_linear_id(db, week_id, child_id)
            promote_input_c: dict = {}
            if old_parent_id:
                promote_input_c["parentId"] = await _resolve_linear_id(db, week_id, old_parent_id)
            else:
                promote_input_c["parentId"] = None
            mutations.append((
                "set_parent",
                MUTATION_ISSUE_UPDATE,
                {"id": child_linear_id, "input": promote_input_c},
            ))

        # Children to add: reparent to target
        for child_id in new_children - old_children:
            if _detect_cycle(tickets_by_id, child_id, identifier):
                raise ValueError(f"Adding {child_id} as child of {identifier} would create a cycle")
            child_linear_id = await _resolve_linear_id(db, week_id, child_id)
            mutations.append((
                "set_parent",
                MUTATION_ISSUE_UPDATE,
                {"id": child_linear_id, "input": {"parentId": target["linear_id"]}},
            ))

        # Sort order for all desired children
        for i, child_id in enumerate(desired_children):
            child_linear_id = await _resolve_linear_id(db, week_id, child_id)
            mutations.append((
                "set_sort_order",
                MUTATION_ISSUE_UPDATE,
                {"id": child_linear_id, "input": {"sortOrder": float(i)}},
            ))

    # --- Field updates ---
    field_input: dict = {}
    if patch.title is not None:
        field_input["title"] = patch.title
    if patch.description is not None:
        field_input["description"] = patch.description
    if patch.labels is not None:
        field_input["labelIds"] = await resolve_label_ids(team_id, patch.labels)
    if patch.status is not None:
        field_input["stateId"] = await resolve_state_id(team_id, patch.status)
    if patch.assignee is not None:
        field_input["assigneeId"] = await _resolve_assignee_linear_id(db, patch.assignee)
    if patch.priority is not None:
        field_input["priority"] = patch.priority
    if patch.points is not None:
        field_input["estimate"] = patch.points

    if field_input:
        mutations.append((
            "set_fields",
            MUTATION_ISSUE_UPDATE,
            {"id": target["linear_id"], "input": field_input},
        ))

    # Execute all mutations in parallel
    tasks = [_execute_mutation(desc, q, v) for desc, q, v in mutations]
    ops = await asyncio.gather(*tasks)

    return identifier, list(ops)


async def create_ticket(
    db: AsyncSession, create: LinearTicketCreate
) -> tuple[str, list[LinearMutationOp]]:
    """Create a new Linear ticket. Returns (new_identifier, list_of_ops)."""
    monday, sunday = resolve_week(None)
    w = await get_or_create_week(db, monday)
    week_id = w.id

    team_name = await get_config_value(db, "linear", "team_name")
    team_id = await get_team_id(team_name)

    create_input: dict = {
        "teamId": team_id,
        "title": create.title,
    }

    if create.description is not None:
        create_input["description"] = create.description
    if create.labels:
        create_input["labelIds"] = await resolve_label_ids(team_id, create.labels)
    if create.status:
        create_input["stateId"] = await resolve_state_id(team_id, create.status)
    else:
        create_input["stateId"] = await get_default_state_id(team_id)
    if create.assignee:
        create_input["assigneeId"] = await _resolve_assignee_linear_id(db, create.assignee)
    if create.priority is not None:
        create_input["priority"] = create.priority
    if create.points is not None:
        create_input["estimate"] = create.points
    if create.parent:
        create_input["parentId"] = await _resolve_linear_id(db, week_id, create.parent)

    # Get active cycle and add ticket to it
    data = await graphql(QUERY_ACTIVE_CYCLE, {"teamId": team_id})
    cycle = data.get("team", {}).get("activeCycle")
    if cycle:
        create_input["cycleId"] = cycle["id"]

    op = await _execute_mutation("create", MUTATION_ISSUE_CREATE, {"input": create_input})
    new_identifier = op.identifier or "unknown"

    return new_identifier, [op]


QUERY_ISSUE_FULL = """
query IssueFull($identifier: String!) {
  issueSearch(query: $identifier, first: 1) {
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


async def _ensure_ticket_in_db(db: AsyncSession, identifier: str) -> None:
    """If a ticket isn't in the current week's DB (e.g. due to Linear API
    eventual consistency after create), fetch it from Linear and insert it."""
    from app.services.people_service import resolve_person

    monday, _ = resolve_week(None)
    w = await get_or_create_week(db, monday)

    existing = await db.execute(
        select(LinearTicket).where(
            LinearTicket.week_id == w.id,
            LinearTicket.identifier == identifier,
        )
    )
    if existing.scalar_one_or_none():
        return  # Already in DB

    data = await graphql(QUERY_ISSUE_FULL, {"identifier": identifier})
    nodes = data.get("issueSearch", {}).get("nodes", [])
    if not nodes:
        return

    issue = nodes[0]
    state = issue.get("state") or {}
    assignee = issue.get("assignee") or {}
    labels = [l.get("name", "") for l in (issue.get("labels", {}).get("nodes") or [])]
    parent = issue.get("parent") or {}
    children = issue.get("children", {}).get("nodes") or []

    person = None
    if assignee.get("id") or assignee.get("email") or assignee.get("name"):
        person = await resolve_person(
            db,
            email=assignee.get("email") or None,
            display_name=assignee.get("name") or assignee.get("id"),
            linear_user_id=assignee.get("id"),
        )

    ticket = LinearTicket(
        week_id=w.id,
        person_id=person.id if person else None,
        linear_id=issue.get("id", ""),
        identifier=issue.get("identifier", ""),
        title=issue.get("title", ""),
        description=issue.get("description"),
        status=state.get("name", "Unknown"),
        status_type=STATUS_TYPE_TO_CATEGORY.get(state.get("type", "unstarted"), "todo"),
        priority=issue.get("priority", 0) or 0,
        priority_label=PRIORITY_MAP.get(issue.get("priority", 0) or 0, "No Priority"),
        labels=labels if labels else None,
        points=issue.get("estimate"),
        in_cycle=True,
        parent_identifier=parent.get("identifier"),
        child_identifiers=[c.get("identifier") for c in children] if children else None,
        url=issue.get("url"),
        linear_created_at=_parse_datetime(issue.get("createdAt")),
        linear_updated_at=_parse_datetime(issue.get("updatedAt")),
    )
    db.add(ticket)
    await db.flush()
