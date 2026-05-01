"""Cycle-agnostic point rollups for declared epics.

Walks an epic's full descendant tree via Linear GraphQL, summing `estimate`
grouped by Linear `state.type` into `done` / `in_progress` / `todo` buckets.
Used by the Epics Police skill to enrich the analysis payload with truthful
progress numbers that span past cycles.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.http_client import resilient_request

logger = logging.getLogger(__name__)

LINEAR_API = "https://api.linear.app/graphql"

_STATE_TO_BUCKET = {
    "completed": "done",
    "started": "in_progress",
    "backlog": "todo",
    "unstarted": "todo",
    "triage": "todo",
    # canceled and any unknown state.type → excluded
}


def _state_bucket(state_type: str) -> str | None:
    """Map a Linear state.type to one of done|in_progress|todo, or None to exclude."""
    return _STATE_TO_BUCKET.get(state_type)


@dataclass
class EpicProgress:
    points: dict[str, int] = field(
        default_factory=lambda: {"done": 0, "in_progress": 0, "todo": 0, "total": 0}
    )
    descendant_count: int = 0
    missing_estimates: int = 0

    def add_node(self, *, estimate: float | int | None, state_type: str) -> None:
        self.descendant_count += 1
        bucket = _state_bucket(state_type)
        if estimate is None:
            self.missing_estimates += 1
            return
        if bucket is None:
            return
        # Linear estimates are floats; round to int for clean point totals.
        pts = int(round(float(estimate)))
        self.points[bucket] += pts
        self.points["total"] += pts

    def to_dict(self) -> dict:
        return {
            "points": dict(self.points),
            "descendant_count": self.descendant_count,
            "missing_estimates": self.missing_estimates,
        }


QUERY_EPIC_CHILDREN = """
query EpicChildren($id: String!, $after: String) {
  issue(id: $id) {
    children(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        identifier
        estimate
        state { type }
        children(first: 1) { nodes { id } }
      }
    }
  }
}
"""


async def _graphql(query: str, variables: dict) -> dict:
    response = await resilient_request(
        "POST",
        LINEAR_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": settings.linear_api_key,
        },
        json={"query": query, "variables": variables},
    )
    if response.status_code != 200:
        snippet = (response.text or "")[:200]
        raise RuntimeError(f"Linear HTTP {response.status_code}: {snippet}")
    try:
        data = response.json()
    except ValueError:
        snippet = (response.text or "")[:200]
        raise RuntimeError(
            f"Linear returned non-JSON body (status {response.status_code}): {snippet}"
        )
    if "errors" in data:
        raise RuntimeError(f"Linear GraphQL errors: {data['errors']}")
    return data.get("data", {})


async def _fetch_children_page(
    issue_ref: str, after: str | None
) -> tuple[list[dict], dict]:
    """Fetch one page of children for the given issue ref.

    `issue_ref` may be either a Linear UUID (preferred for inner recursion — we
    have it from earlier in the walk) or an identifier like "SC-1234" (used for
    the entry call). Linear's `issue(id:)` accepts both.
    """
    variables: dict = {"id": issue_ref}
    if after:
        variables["after"] = after
    data = await _graphql(QUERY_EPIC_CHILDREN, variables)
    issue = data.get("issue") or {}
    if not issue:
        raise RuntimeError(f"Linear: issue not found for ref {issue_ref!r}")
    children = issue.get("children", {}) or {}
    return children.get("nodes", []) or [], children.get("pageInfo", {}) or {}


DEFAULT_DEPTH_CAP = 8
DEFAULT_VISIT_CAP = 5_000
_PER_LEVEL_CONCURRENCY = 5


async def _walk_epic(
    identifier: str,
    *,
    depth_cap: int = DEFAULT_DEPTH_CAP,
    visit_cap: int = DEFAULT_VISIT_CAP,
) -> EpicProgress:
    """Walk descendants of `identifier` (a Linear identifier or UUID) and
    return aggregated progress. Raises RuntimeError on Linear failures the
    caller cannot recover from per-epic (e.g. issue-not-found, GraphQL errors)."""
    progress = EpicProgress()
    visited: set[str] = set()

    # Stack of (issue_ref, depth) to walk. Start one level below the epic.
    frontier: list[tuple[str, int]] = [(identifier, 0)]

    while frontier:
        # Pop one level's worth at a time so we can fan out concurrently.
        current_level = frontier
        frontier = []
        for ref, depth in current_level:
            after: str | None = None
            while True:
                nodes, page_info = await _fetch_children_page(ref, after)
                for node in nodes:
                    node_id = node.get("id")
                    if not node_id or node_id in visited:
                        continue
                    visited.add(node_id)
                    if len(visited) > visit_cap:
                        raise RuntimeError(
                            f"Visit cap {visit_cap} exceeded walking {identifier}"
                        )
                    state_type = (node.get("state") or {}).get("type") or ""
                    progress.add_node(
                        estimate=node.get("estimate"), state_type=state_type
                    )
                    has_grandchildren = bool(
                        ((node.get("children") or {}).get("nodes")) or []
                    )
                    if has_grandchildren:
                        if depth + 1 >= depth_cap:
                            logger.warning(
                                "Depth cap hit walking %s at %s",
                                identifier,
                                node.get("identifier"),
                            )
                            continue
                        frontier.append((node_id, depth + 1))
                if page_info.get("hasNextPage") and page_info.get("endCursor"):
                    after = page_info["endCursor"]
                else:
                    break

    return progress


async def fetch_epic_progress(
    identifiers: list[str],
) -> tuple[dict[str, EpicProgress | None], list[str]]:
    """Resolve progress for many epic identifiers concurrently.

    Returns `(results, warnings)`:
      - `results[id]` is `None` for any per-epic failure (other epics still resolve).
      - `warnings` lists per-epic failure reasons, one entry per failed identifier.

    Concurrency is capped at `_PER_LEVEL_CONCURRENCY` to stay well under
    Linear's rate limits.
    """
    seen: list[str] = []
    deduped: list[str] = []
    for ident in identifiers:
        if ident in seen:
            continue
        seen.append(ident)
        deduped.append(ident)

    semaphore = asyncio.Semaphore(_PER_LEVEL_CONCURRENCY)
    warnings: list[str] = []
    results: dict[str, EpicProgress | None] = {}

    async def _one(ident: str) -> None:
        async with semaphore:
            try:
                results[ident] = await _walk_epic(ident)
            except Exception as exc:  # noqa: BLE001
                # Per-epic isolation: log + record warning, but other epics keep going.
                logger.warning("Epic progress failed for %s: %s", ident, exc)
                results[ident] = None
                warnings.append(f"{ident}: {exc}")

    await asyncio.gather(*(_one(ident) for ident in deduped))
    # Preserve input order in the returned mapping.
    ordered = {ident: results[ident] for ident in deduped}
    return ordered, warnings
