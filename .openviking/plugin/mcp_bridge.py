#!/usr/bin/env python3
"""Minimal MCP bridge that wraps the OpenViking HTTP API.
Runs via stdio - Claude Code spawns this automatically.
Connects to an already-running openviking-server.
"""
import json
import sys
import warnings
warnings.filterwarnings("ignore")

from mcp.server.fastmcp import FastMCP
import openviking as ov

PORT = sys.argv[1] if len(sys.argv) > 1 else "1934"
_client = None

def get_client():
    global _client
    if _client is None:
        _client = ov.SyncHTTPClient(url=f"http://localhost:{PORT}")
        _client.initialize()
    return _client


def _discover_memory_roots(client):
    """Discover memory URIs by listing viking://user/ and viking://agent/."""
    roots = []
    for scope in ("user", "agent"):
        try:
            items = client.ls(f"viking://{scope}/")
            entries = items if isinstance(items, list) else getattr(items, "entries", []) or []
            for item in entries:
                uri = getattr(item, "uri", None) or (item.get("uri") if isinstance(item, dict) else "")
                if uri:
                    roots.append(f"{uri}/memories/")
        except Exception:
            pass
    return roots


mcp = FastMCP("openviking-bridge")

@mcp.tool()
def search(query: str, top_k: int = 10) -> str:
    """Search OpenViking memories and resources. Use this to find past decisions, patterns, context, or any ingested documentation. Increase top_k for broader results."""
    client = get_client()
    roots = _discover_memory_roots(client) + ["viking://resources/"]
    all_results = []
    per_root_limit = max(top_k, 10)
    for uri in roots:
        try:
            results = client.find(query=query, target_uri=uri, limit=per_root_limit)
            for key in ("memories", "resources", "skills"):
                items = getattr(results, key, []) or []
                for item in items:
                    score = getattr(item, "score", 0) or 0
                    item_uri = getattr(item, "uri", "") or ""
                    abstract = getattr(item, "abstract", "") or ""
                    all_results.append({"uri": item_uri, "score": score, "abstract": abstract})
        except Exception:
            continue

    all_results.sort(key=lambda x: x["score"], reverse=True)
    all_results = all_results[:top_k]

    if not all_results:
        return "No results found."

    lines = []
    for i, r in enumerate(all_results, 1):
        lines.append(f"{i}. [{r['score']:.3f}] {r['uri']}")
        if r["abstract"]:
            lines.append(f"   {r['abstract'][:300]}")
    return "\n".join(lines)


@mcp.tool()
def read_resource(uri: str) -> str:
    """Read the full content of an OpenViking resource by URI. Use after search to get full details."""
    client = get_client()
    try:
        content = client.read(uri)
        return str(content) if content else "Empty or not found."
    except Exception as e:
        return f"Error reading {uri}: {e}"


@mcp.tool()
def list_memories() -> str:
    """List all stored memories and resources in OpenViking."""
    client = get_client()
    memory_roots = _discover_memory_roots(client)
    roots = [(r, r) for r in memory_roots] + [("Resources", "viking://resources/")]
    lines = []
    for label, uri in roots:
        try:
            items = client.ls(uri)
            entries = items if isinstance(items, list) else getattr(items, "entries", []) or []
            if not entries:
                lines.append(f"\n{label}: (empty)")
                continue
            lines.append(f"\n{label}: ({len(entries)} items)")
            for item in entries:
                name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else str(item))
                item_uri = getattr(item, "uri", None) or (item.get("uri") if isinstance(item, dict) else "")
                lines.append(f"  {name} -> {item_uri}")
        except Exception as e:
            lines.append(f"\n{label}: (error: {e})")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
