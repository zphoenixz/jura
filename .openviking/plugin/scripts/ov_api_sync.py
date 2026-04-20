#!/usr/bin/env python3
"""OpenViking API sync — fetch formatted data from Management API and ingest into OV.

Usage:
    ov_api_sync.py sync <workspace> [--week YYYY-MM-DD] [--source SOURCE]
    ov_api_sync.py status <workspace> [--week YYYY-MM-DD]

Fetches pre-rendered markdown from the Management API's /formatted endpoints,
writes to a staging directory, and ingests into OpenViking. Each run does a
clean nuke + re-ingest for the target week+source, so running 100 times on
the same week produces the same result with no duplicates.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError


# ---------------------------------------------------------------------------
# Paths — derived from script location, overridable via env vars.
#
# When run from the repo (default), paths resolve relative to SCRIPT_DIR
# (jura repo). When run from a staged copy under ~/.openviking/job/<ws>/
# (launchd-triggered to dodge TCC on protected home folders), the plist
# sets JURA_ENV_FILE / JURA_SETTINGS_DIR / JURA_STAGING_ROOT explicitly
# so the script never tries to read the original repo.
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent          # .openviking/plugin/scripts/
_DEFAULT_JURA_ROOT = SCRIPT_DIR.parent.parent.parent  # jura repo root (when in-repo)

ENV_FILE = Path(os.environ.get("JURA_ENV_FILE") or (_DEFAULT_JURA_ROOT / ".env")).resolve()
SETTINGS_DIR = Path(os.environ.get("JURA_SETTINGS_DIR") or (_DEFAULT_JURA_ROOT / ".jura")).resolve()
STAGING_ROOT = Path(os.environ.get("JURA_STAGING_ROOT") or (_DEFAULT_JURA_ROOT / ".openviking" / "staging")).resolve()

SOURCES = ["slack", "linear", "meets", "epics"]
OV_TARGET_ROOT = "viking://resources/api"


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------

def load_env() -> None:
    """Source .env file into os.environ."""
    if not ENV_FILE.exists():
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_workspace_settings(name: str) -> dict:
    """Load workspace config from .jura/<name>.json."""
    path = SETTINGS_DIR / f"{name}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def list_workspaces() -> List[str]:
    """Return list of workspace names from .jura/*.json files."""
    if not SETTINGS_DIR.is_dir():
        return []
    return [p.stem for p in sorted(SETTINGS_DIR.glob("*.json"))]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def current_monday() -> str:
    """Return the Monday of the current week as YYYY-MM-DD."""
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def ov_cmd(args: List[str], port: int, timeout: int = 120) -> Tuple[bool, str]:
    """Run an ov CLI command targeting the correct OV instance.

    Uses the same HOME-override trick as the jura bash script: creates a temp
    directory with an ovcli.conf pointing to the right port.
    """
    tmp_home = tempfile.mkdtemp()
    ov_conf_dir = Path(tmp_home) / ".openviking"
    ov_conf_dir.mkdir()
    (ov_conf_dir / "ovcli.conf").write_text(
        json.dumps({"url": f"http://127.0.0.1:{port}"})
    )

    env = os.environ.copy()
    env["HOME"] = tmp_home
    env["OV_URL"] = f"http://127.0.0.1:{port}"
    cmd = ["ov"] + args
    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout + result.stderr
        ok = result.returncode == 0
        if not ok and "error" in output.lower():
            return False, output.strip()
        return ok, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"Timeout ({timeout}s) running: {' '.join(cmd)}"
    except FileNotFoundError:
        return False, "ov CLI not found. Is openviking installed?"
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


def ov_health(port: int) -> bool:
    """Check if OV server is reachable."""
    ok, output = ov_cmd(["ls", "viking://resources/"], port, timeout=10)
    return ok or "viking://" in output


def api_health(api_url: str) -> bool:
    """Check if Management API is reachable."""
    try:
        resp = urlopen(Request(f"{api_url}/api/v1/health"), timeout=5)
        return resp.status == 200
    except Exception:
        return False


def fetch_formatted(api_url: str, source: str, week: str, retries: int = 3) -> List[dict]:
    """Fetch formatted documents from Management API.

    Retries network errors with exponential backoff (2s, 4s). JSON decode
    errors are NOT retried — they indicate a server-side bug, not a transient
    failure, and retrying just wastes time.
    """
    url = f"{api_url}/api/v1/{source}/formatted?week={week}"
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            resp = urlopen(Request(url), timeout=60)
            return json.loads(resp.read().decode("utf-8"))
        except URLError as e:
            last_err = str(e)
            if attempt < retries:
                delay = 2 ** attempt
                log(f"Fetch {source} attempt {attempt}/{retries} failed: {e} — retrying in {delay}s", "WARN")
                time.sleep(delay)
        except json.JSONDecodeError as e:
            log(f"Invalid JSON from {source}: {e}", "ERROR")
            return []
    log(f"Failed to fetch {source} after {retries} attempts: {last_err}", "ERROR")
    return []


def ensure_ov_parents(week: str, port: int) -> None:
    """Create parent directories in OV: viking://resources/api/{week}."""
    ov_cmd(["mkdir", OV_TARGET_ROOT], port, timeout=10)
    ov_cmd(["mkdir", f"{OV_TARGET_ROOT}/{week}"], port, timeout=10)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_sync(workspace: str, port: int, api_url: str, week: str,
             source_filter: Optional[str] = None) -> None:
    """Fetch formatted data from API, write to staging, ingest into OV."""

    sources = [source_filter] if source_filter else SOURCES
    staging = STAGING_ROOT / workspace

    if not ov_health(port):
        log(f"OV server not reachable at port {port}", "ERROR")
        sys.exit(1)

    if not api_health(api_url):
        log(f"Management API not reachable at {api_url}", "ERROR")
        sys.exit(1)

    log(f"API sync: workspace={workspace}, port={port}, week={week}")
    log(f"  API: {api_url}")
    log(f"  Sources: {', '.join(sources)}")

    # Step 1: Clean previous staging files
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    log("Staging directory cleaned")

    # Step 2: Fetch all sources in parallel
    log("Fetching formatted data from API...")
    results: Dict[str, List[dict]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch_formatted, api_url, src, week): src
            for src in sources
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                docs = future.result()
                results[src] = docs
                log(f"  {src}: {len(docs)} documents")
            except Exception as e:
                log(f"  {src}: fetch failed — {e}", "ERROR")
                results[src] = []

    # Step 3: Write staging files, nuke OV target, re-ingest
    ensure_ov_parents(week, port)
    total_ingested = 0
    errors = 0

    for source in sources:
        docs = results.get(source, [])
        if not docs:
            log(f"  {source}: no documents, skipping")
            continue

        # Write .md files to staging/{source}/
        source_dir = staging / source
        source_dir.mkdir(parents=True)
        for doc in docs:
            filename = doc["title"]
            if not filename.endswith(".md"):
                filename += ".md"
            (source_dir / filename).write_text(doc["content"], encoding="utf-8")

        target = f"{OV_TARGET_ROOT}/{week}/{source}"

        # Nuke old OV content for this week+source
        log(f"  {source}: removing {target}")
        ok, out = ov_cmd(["rm", target, "--recursive"], port, timeout=30)
        if not ok and "not found" not in out.lower() and "does not exist" not in out.lower():
            log(f"    rm note: {out}", "WARN")

        # Ingest fresh
        log(f"  {source}: ingesting {len(docs)} documents")
        ok, out = ov_cmd([
            "add-resource", str(source_dir),
            "--to", target,
            "--include", "*.md",
            "--wait", "--timeout", "600",
        ], port, timeout=660)

        if ok:
            total_ingested += len(docs)
            log(f"  {source}: done ({len(docs)} docs)")
        else:
            # Check if data landed despite CLI error (common with large batches)
            log(f"  {source}: add-resource returned error, checking...", "WARN")
            time.sleep(3)
            check_ok, check_out = ov_cmd(["ls", target + "/"], port, timeout=10)
            if check_ok and "viking://" in check_out:
                log(f"  {source}: data found — ingestion succeeded despite error")
                ov_cmd(["wait"], port, timeout=300)
                total_ingested += len(docs)
            else:
                log(f"  {source}: ingest failed — {out}", "ERROR")
                errors += 1

    # Final drain
    log("Draining OV processing queue...")
    ov_cmd(["wait"], port, timeout=300)

    log(f"Sync complete. {total_ingested} documents ingested across {len(sources)} sources. {errors} errors.")
    if errors > 0:
        sys.exit(1)


def cmd_status(workspace: str, port: int, api_url: str, week: str) -> None:
    """Show what's in OV and what's available from the API."""

    print(f"Workspace: {workspace}")
    print(f"OV port:   {port}")
    print(f"API URL:   {api_url}")
    print(f"Week:      {week}")
    print()

    # Check connectivity
    ov_ok = ov_health(port)
    api_ok = api_health(api_url)
    print(f"OV server:       {'reachable' if ov_ok else 'NOT REACHABLE'}")
    print(f"Management API:  {'reachable' if api_ok else 'NOT REACHABLE'}")
    print()

    # What's in OV under the api tree
    if ov_ok:
        print("Current OV content (viking://resources/api/):")
        ok, out = ov_cmd(["ls", f"{OV_TARGET_ROOT}/"], port, timeout=10)
        if ok and out.strip():
            for line in out.strip().splitlines():
                print(f"  {line}")
        else:
            print("  (empty)")
        print()

    # What's available from the API
    if api_ok:
        print(f"Available from API for week {week}:")
        for source in SOURCES:
            docs = fetch_formatted(api_url, source, week)
            print(f"  {source}: {len(docs)} documents")
        print()

    # Staging directory
    staging = STAGING_ROOT / workspace
    if staging.exists():
        file_count = sum(1 for _ in staging.rglob("*.md"))
        print(f"Staging: {staging} ({file_count} files from last run)")
    else:
        print(f"Staging: {staging} (no previous run)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_env()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Commands:")
        print("  sync <workspace> [--week YYYY-MM-DD] [--source SOURCE]")
        print("  status <workspace> [--week YYYY-MM-DD]")
        print()
        print(f"Sources: {', '.join(SOURCES)}")
        print(f"Default week: current Monday ({current_monday()})")
        print()
        print("Configured workspaces:")
        for ws in list_workspaces():
            settings = load_workspace_settings(ws)
            api_url = settings.get("api_url", "(not configured)")
            port = settings.get("port", 0)
            print(f"  {ws} (port {port}, api: {api_url})")
        sys.exit(0)

    command = sys.argv[1]

    # Parse args
    workspace = None
    week = None
    source = None

    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--week" and i + 1 < len(sys.argv):
            i += 1
            week = sys.argv[i]
        elif arg == "--source" and i + 1 < len(sys.argv):
            i += 1
            source = sys.argv[i]
            if source not in SOURCES:
                log(f"Unknown source: {source}. Valid: {', '.join(SOURCES)}", "ERROR")
                sys.exit(1)
        elif not arg.startswith("--"):
            workspace = arg
        else:
            log(f"Unknown flag: {arg}", "ERROR")
            sys.exit(1)
        i += 1

    if not workspace:
        log("Workspace name required. Available:", "ERROR")
        for ws in list_workspaces():
            print(f"  {ws}")
        sys.exit(1)

    settings = load_workspace_settings(workspace)
    if not settings:
        log(f"Unknown workspace: {workspace}", "ERROR")
        sys.exit(1)

    port = settings.get("port", 0)
    if not port:
        log(f"No port configured for workspace '{workspace}'", "ERROR")
        sys.exit(1)

    api_url = settings.get("api_url", "")
    if not api_url:
        log(f"No api_url configured for workspace '{workspace}'", "ERROR")
        log(f"Add \"api_url\": \"http://localhost:8100\" to {SETTINGS_DIR / (workspace + '.json')}")
        sys.exit(1)

    if not week:
        week = current_monday()

    if command == "sync":
        cmd_sync(workspace, port, api_url, week, source_filter=source)
    elif command == "status":
        cmd_status(workspace, port, api_url, week)
    else:
        log(f"Unknown command: {command}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
