"""Cached name-to-ID resolution for Linear labels, workflow states, and team."""

import time

from app.services.linear_fetcher import graphql, QUERY_TEAM

QUERY_TEAM_LABELS_AND_STATES = """
query TeamLabelsAndStates($teamId: String!) {
  team(id: $teamId) {
    labels { nodes { id name } }
    states { nodes { id name type } }
  }
}
"""

# Module-level cache: {team_id: {"labels": {...}, "states": {...}, "expires_at": float}}
_cache: dict[str, dict] = {}
CACHE_TTL = 300  # 5 minutes


async def get_team_id(team_name: str) -> str:
    """Resolve team name to Linear team ID."""
    data = await graphql(QUERY_TEAM, {"name": team_name})
    nodes = data.get("teams", {}).get("nodes", [])
    if not nodes:
        raise ValueError(f"Linear team '{team_name}' not found")
    return nodes[0]["id"]


async def _ensure_cache(team_id: str) -> dict:
    """Populate or return cached labels/states for a team."""
    now = time.monotonic()
    if team_id in _cache and _cache[team_id]["expires_at"] > now:
        return _cache[team_id]

    data = await graphql(QUERY_TEAM_LABELS_AND_STATES, {"teamId": team_id})
    team_data = data.get("team", {})

    labels = {n["name"]: n["id"] for n in team_data.get("labels", {}).get("nodes", [])}
    states = {}
    for n in team_data.get("states", {}).get("nodes", []):
        states[n["name"]] = {"id": n["id"], "type": n.get("type", "")}

    _cache[team_id] = {"labels": labels, "states": states, "expires_at": now + CACHE_TTL}
    return _cache[team_id]


async def resolve_label_ids(team_id: str, label_names: list[str]) -> list[str]:
    """Resolve label names to Linear label IDs. Raises ValueError for unknown labels."""
    cache = await _ensure_cache(team_id)
    ids = []
    unknown = []
    for name in label_names:
        lid = cache["labels"].get(name)
        if lid:
            ids.append(lid)
        else:
            unknown.append(name)
    if unknown:
        raise ValueError(f"Unknown Linear labels: {unknown}. Available: {list(cache['labels'].keys())}")
    return ids


async def resolve_state_id(team_id: str, state_name: str) -> str:
    """Resolve a workflow state name to its Linear ID. Raises ValueError if not found."""
    cache = await _ensure_cache(team_id)
    state = cache["states"].get(state_name)
    if not state:
        raise ValueError(f"Unknown Linear state '{state_name}'. Available: {list(cache['states'].keys())}")
    return state["id"]


async def get_default_state_id(team_id: str) -> str:
    """Get the default unstarted state ID for a team."""
    cache = await _ensure_cache(team_id)
    for name, info in cache["states"].items():
        if info["type"] in ("backlog", "unstarted", "triage"):
            return info["id"]
    # Fallback: first state
    if cache["states"]:
        return next(iter(cache["states"].values()))["id"]
    raise ValueError("No workflow states found for team")


def clear_cache():
    """Clear the lookup cache (for testing)."""
    _cache.clear()
