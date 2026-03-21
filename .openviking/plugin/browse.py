#!/usr/bin/env python3
"""Browse OpenViking memories interactively."""
import sys
import warnings
warnings.filterwarnings("ignore")

import openviking as ov

port = sys.argv[1] if len(sys.argv) > 1 else "1934"
client = ov.SyncHTTPClient(url=f"http://localhost:{port}")
client.initialize()

roots = [
    ("User Memories",  "viking://user/memories/"),
    ("Agent Memories", "viking://agent/memories/"),
    ("Resources",      "viking://resources/"),
]

for label, uri in roots:
    try:
        items = client.ls(uri)
        entries = items if isinstance(items, list) else getattr(items, "entries", []) or []
        if not entries:
            print(f"\n--- {label} (empty) ---")
            continue
        print(f"\n--- {label} ({len(entries)} items) ---")
        for item in entries:
            name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else str(item))
            item_uri = getattr(item, "uri", None) or (item.get("uri") if isinstance(item, dict) else "")
            print(f"  {name}  ->  {item_uri}")
    except Exception as e:
        print(f"\n--- {label} (error: {e}) ---")

while True:
    print()
    try:
        query = input("Search (or empty to quit): ")
    except (EOFError, KeyboardInterrupt):
        break
    query = query.strip()
    if not query:
        break

    found = False
    for label, uri in roots:
        try:
            results = client.find(query=query, target_uri=uri, limit=5)
            items = []
            for key in ("memories", "resources", "skills"):
                items.extend(getattr(results, key, []) or [])
            if not items:
                continue
            found = True
            print(f'\n=== {label}: "{query}" ===')
            for i, m in enumerate(items, 1):
                score = getattr(m, "score", 0) or 0
                m_uri = getattr(m, "uri", "") or ""
                abstract = getattr(m, "abstract", "") or ""
                print(f"  {i}. [{score:.3f}] {m_uri}")
                if abstract:
                    print(f"     {abstract[:200]}")
        except Exception as e:
            print(f"  Error searching {label}: {e}")

    if not found:
        print("  No results found.")

client.close()
