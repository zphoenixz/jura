#!/usr/bin/env python3
"""OpenViking incremental resource sync.

Syncs local files to an OpenViking instance, re-ingesting only files
that actually changed. Tracks state via per-workspace manifest files.

Usage:
    ov_sync.py bootstrap <workspace>   # nuke + full re-ingest
    ov_sync.py sync <workspace>        # incremental sync
    ov_sync.py status <workspace>      # dry-run: show what changed

Workspace configuration is read from .jura/<workspace>.json settings files:
    { "port": 1934, "source_dir": "/path/to/docs", "include": ["*.md"], "target_root": "viking://resources" }

Sync state (file hashes, last commit) is stored in .openviking/manifests/<workspace>.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Set, Tuple


# ---------------------------------------------------------------------------
# Paths — derived from script location (follows symlinks)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent          # .openviking/plugin/scripts/
JURA_ROOT = SCRIPT_DIR.parent.parent.parent           # jura repo root
ENV_FILE = JURA_ROOT / ".env"
MANIFEST_DIR = JURA_ROOT / ".openviking" / "manifests"
SETTINGS_DIR = JURA_ROOT / ".jura"


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
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def load_workspace_settings(name: str) -> dict:
    """Load workspace config from .jura/<name>.json."""
    path = SETTINGS_DIR / f"{name}.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def workspace_port(name: str) -> int:
    """Resolve workspace name to port from settings file."""
    return load_workspace_settings(name).get("port", 0)


def workspace_source(name: str) -> str:
    """Resolve workspace name to source directory from settings file."""
    return load_workspace_settings(name).get("source_dir", "")


def list_workspaces() -> List[str]:
    """Return list of workspace names from .jura/*.json files."""
    if not SETTINGS_DIR.is_dir():
        return []
    return [p.stem for p in sorted(SETTINGS_DIR.glob("*.json"))]


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class SyncConfig:
    workspace: str = ""
    port: int = 0
    source_dir: str = ""                               # absolute path
    target_root: str = "viking://resources"
    include: List[str] = field(default_factory=lambda: ["*.md", "*.json"])
    last_sync_commit: str = ""
    files: Dict[str, dict] = field(default_factory=dict)  # relative_path -> {sha256, ov_uri, synced_at}

    def to_dict(self) -> dict:
        """Manifest stores only sync state, not config."""
        return {
            "last_sync_commit": self.last_sync_commit,
            "files": self.files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SyncConfig":
        """Load sync state from manifest."""
        return cls(
            last_sync_commit=d.get("last_sync_commit", ""),
            files=d.get("files", {}),
        )

    @property
    def manifest_path(self) -> Path:
        return MANIFEST_DIR / f"{self.workspace}.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ov_cmd(args: List[str], port: int, timeout: int = 120) -> Tuple[bool, str]:
    """Run an ov CLI command targeting the correct OV instance.

    Uses the same HOME-override trick as the jura bash script: creates a temp
    directory with an ovcli.conf pointing to the right port, then sets HOME
    so the ov CLI reads it instead of the global ~/.openviking/ovcli.conf.
    """
    import tempfile
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
        import shutil
        shutil.rmtree(tmp_home, ignore_errors=True)


def ov_health(port: int) -> bool:
    """Check if OV server is reachable."""
    ok, output = ov_cmd(["ls", "viking://resources/"], port, timeout=10)
    return ok or "viking://" in output


def ov_uri_exists(uri: str, port: int) -> bool:
    """Check if a URI exists in OV."""
    ok, output = ov_cmd(["stat", uri], port, timeout=10)
    return ok and "error" not in output.lower()


def ov_mkdir_chain(uri: str, port: int) -> bool:
    """Create a URI directory and all missing parents."""
    prefix = "viking://resources"
    if not uri.startswith(prefix):
        log(f"URI must start with {prefix}: {uri}", "ERROR")
        return False
    remainder = uri[len(prefix):].strip("/")
    if not remainder:
        return True
    segments = remainder.split("/")
    current = prefix
    for seg in segments:
        current = f"{current}/{seg}"
        if not ov_uri_exists(current, port):
            ok, out = ov_cmd(["mkdir", current], port)
            if not ok:
                log(f"Failed to mkdir {current}: {out}", "ERROR")
                return False
            log(f"  mkdir {current}")
    return True


def git_run(args: List[str], cwd: str) -> Tuple[bool, str]:
    """Run a git command in a specific directory."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, cwd=cwd,
        )
        return result.returncode == 0, result.stdout.strip()
    except FileNotFoundError:
        return False, "git not found"


def git_head_sha(cwd: str) -> str:
    ok, sha = git_run(["rev-parse", "HEAD"], cwd)
    return sha if ok else ""


def file_matches_patterns(name: str, patterns: List[str]) -> bool:
    return any(fnmatch(name, p) for p in patterns)


def load_manifest(workspace: str) -> SyncConfig:
    manifest = MANIFEST_DIR / f"{workspace}.json"
    if manifest.exists():
        with open(manifest) as f:
            return SyncConfig.from_dict(json.load(f))
    return SyncConfig(workspace=workspace)


def save_manifest(config: SyncConfig) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.manifest_path, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
    log(f"Manifest saved to {config.manifest_path}")


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_local_files(config: SyncConfig) -> Dict[str, Path]:
    """Use git ls-files to find tracked files matching include patterns."""
    source_abs = Path(config.source_dir)
    if not source_abs.is_dir():
        log(f"Source directory not found: {source_abs}", "ERROR")
        sys.exit(1)

    ok, git_root = git_run(["rev-parse", "--show-toplevel"], str(source_abs))
    if not ok:
        log("Not a git repository, falling back to filesystem walk", "WARN")
        return _discover_local_files_fs(config)

    # Get git-tracked files within source_dir
    ok, out = git_run(["ls-files", "--", str(source_abs)], git_root)
    if not ok:
        return {}

    files: Dict[str, Path] = {}
    for line in out.splitlines():
        if not line:
            continue
        abs_path = Path(git_root) / line
        if not abs_path.is_file():
            continue
        if not file_matches_patterns(abs_path.name, config.include):
            continue
        try:
            rel = str(abs_path.relative_to(source_abs))
        except ValueError:
            continue
        files[rel] = abs_path
    return files


def _discover_local_files_fs(config: SyncConfig) -> Dict[str, Path]:
    """Fallback: walk source_dir filesystem for non-git directories."""
    source_abs = Path(config.source_dir)
    files: Dict[str, Path] = {}
    for abs_path in source_abs.rglob("*"):
        if not abs_path.is_file():
            continue
        if not file_matches_patterns(abs_path.name, config.include):
            continue
        rel = str(abs_path.relative_to(source_abs))
        files[rel] = abs_path
    return files


def find_changed_files_git(config: SyncConfig) -> Set[str]:
    """Use git to find files changed since last sync commit."""
    source_abs = Path(config.source_dir)

    # Find the git repo root that contains our source dir
    ok, git_root = git_run(["rev-parse", "--show-toplevel"], str(source_abs))
    if not ok:
        return set()

    # Get source_dir relative to git root
    try:
        source_rel = str(source_abs.relative_to(git_root))
    except ValueError:
        return set()

    changed: Set[str] = set()

    if config.last_sync_commit:
        ok, out = git_run(["diff", "--name-only", config.last_sync_commit, "HEAD", "--", source_rel], git_root)
        if ok and out:
            changed.update(out.splitlines())

    ok, out = git_run(["diff", "--name-only", "HEAD", "--", source_rel], git_root)
    if ok and out:
        changed.update(out.splitlines())

    ok, out = git_run(["ls-files", "--others", "--exclude-standard", "--", source_rel], git_root)
    if ok and out:
        changed.update(out.splitlines())

    # Convert from repo-relative to source-relative
    prefix = source_rel + "/"
    result: Set[str] = set()
    for f in changed:
        if f.startswith(prefix):
            result.add(f[len(prefix):])
        elif f == source_rel:
            continue
        else:
            result.add(f)
    return result


# ---------------------------------------------------------------------------
# URI mapping
# ---------------------------------------------------------------------------

def file_to_ov_uri(rel_path: str, target_root: str) -> str:
    """Map a relative file path to its OV directory URI."""
    stem = str(Path(rel_path).with_suffix(""))
    return f"{target_root}/{stem}"


def file_to_parent_uri(rel_path: str, target_root: str) -> str:
    """Get the parent URI for a file."""
    parent = str(Path(rel_path).parent)
    if parent == ".":
        return target_root
    return f"{target_root}/{parent}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(config: SyncConfig) -> None:
    """Dry-run: show what would be synced."""
    log(f"Workspace: {config.workspace}")
    log(f"Instance: port {config.port}")
    log(f"Source: {config.source_dir}")
    log(f"Target: {config.target_root}")
    log(f"Last sync commit: {config.last_sync_commit or '(never)'}")
    log(f"Tracked files: {len(config.files)}")
    print()

    local_files = discover_local_files(config)

    adds, updates, skips, deletes = [], [], [], []

    for rel, abs_path in sorted(local_files.items()):
        current_sha = sha256_file(abs_path)
        ov_uri = file_to_ov_uri(rel, config.target_root)

        if rel in config.files:
            if config.files[rel]["sha256"] == current_sha:
                skips.append(rel)
            else:
                updates.append((rel, ov_uri))
        else:
            adds.append((rel, ov_uri))

    tracked_set = set(config.files.keys())
    local_set = set(local_files.keys())
    for rel in sorted(tracked_set - local_set):
        ov_uri = config.files[rel].get("ov_uri", file_to_ov_uri(rel, config.target_root))
        deletes.append((rel, ov_uri))

    print(f"  {'NEW':>8}  {len(adds)} files")
    for rel, uri in adds:
        print(f"           + {rel}  ->  {uri}")

    print(f"  {'UPDATE':>8}  {len(updates)} files")
    for rel, uri in updates:
        print(f"           ~ {rel}  ->  {uri}")

    print(f"  {'DELETE':>8}  {len(deletes)} files")
    for rel, uri in deletes:
        print(f"           - {rel}  ->  {uri}")

    print(f"  {'SKIP':>8}  {len(skips)} files (unchanged)")
    print()
    total = len(adds) + len(updates) + len(deletes)
    log(f"Total actions: {total} ({'nothing to do' if total == 0 else 'run sync to apply'})")


def cmd_bootstrap(config: SyncConfig, dry_run: bool = False) -> None:
    """Nuke all resources and do a full re-ingest, then build manifest."""
    port = config.port
    source_abs = Path(config.source_dir)

    if not source_abs.is_dir():
        log(f"Source directory not found: {source_abs}", "ERROR")
        sys.exit(1)

    if not ov_health(port):
        log(f"OV server not reachable at port {port}", "ERROR")
        sys.exit(1)

    log(f"Bootstrap: workspace={config.workspace}, port={port}")
    log(f"  source={config.source_dir}")
    log(f"  target={config.target_root}")

    # Step 1: Nuke existing resources
    log("Step 1: Removing all existing resources...")
    if not dry_run:
        ok, out = ov_cmd(["rm", config.target_root, "--recursive"], port, timeout=60)
        if ok:
            log(f"  Removed {config.target_root}")
        else:
            if "not found" in out.lower() or "does not exist" in out.lower():
                log("  Nothing to remove (clean state)")
            else:
                log(f"  rm output: {out}")

    # Step 2: Full directory ingest
    log(f"Step 2: Ingesting {source_abs} -> {config.target_root} ...")
    include_arg = ",".join(config.include)

    add_args = [
        "add-resource", str(source_abs),
        "--to", config.target_root,
        "--reason", f"bootstrap {datetime.now(timezone.utc).isoformat()}",
        "--include", include_arg,
        "--wait", "--timeout", "600",
    ]

    if not dry_run:
        log(f"  Running: ov {' '.join(add_args)}")
        ok, out = ov_cmd(add_args, port, timeout=660)
        if not ok:
            log(f"  add-resource returned error: {out}", "WARN")
            log("  Checking if ingestion completed anyway...")
            time.sleep(5)
            check_ok, check_out = ov_cmd(["ls", config.target_root + "/"], port, timeout=15)
            if check_ok and "viking://" in check_out:
                log("  Data found — ingestion succeeded despite CLI error. Draining queue...")
                ov_cmd(["wait"], port, timeout=300)
            else:
                log("  No data found. Ingestion truly failed.", "ERROR")
                sys.exit(1)
        else:
            log("  Ingest complete")
            for line in out.splitlines():
                if "processed" in line.lower() or "queue" in line.lower():
                    log(f"  {line}")
    else:
        log(f"  [DRY RUN] Would run: ov {' '.join(add_args)}")

    # Step 3: Build manifest from local files
    log("Step 3: Building manifest...")
    local_files = discover_local_files(config)
    config.files = {}
    now = datetime.now(timezone.utc).isoformat()
    for rel, abs_path in sorted(local_files.items()):
        config.files[rel] = {
            "sha256": sha256_file(abs_path),
            "ov_uri": file_to_ov_uri(rel, config.target_root),
            "synced_at": now,
        }
    config.last_sync_commit = git_head_sha(str(source_abs))
    log(f"  Tracked {len(config.files)} files, commit={config.last_sync_commit[:8] if config.last_sync_commit else 'n/a'}")

    if not dry_run:
        save_manifest(config)

    log("Bootstrap complete.")


def cmd_sync(config: SyncConfig, dry_run: bool = False) -> None:
    """Incremental sync: only re-ingest changed files."""
    port = config.port

    if not config.last_sync_commit:
        log("No previous sync found. Run 'bootstrap' first.", "ERROR")
        sys.exit(1)

    if not ov_health(port):
        log(f"OV server not reachable at port {port}", "ERROR")
        sys.exit(1)

    log(f"Sync: workspace={config.workspace}, port={port}")
    log(f"  source={config.source_dir}")
    log(f"Last sync: {config.last_sync_commit[:8]}")

    local_files = discover_local_files(config)

    adds: List[Tuple[str, Path]] = []
    updates: List[Tuple[str, Path]] = []
    deletes: List[str] = []

    for rel, abs_path in local_files.items():
        current_sha = sha256_file(abs_path)
        if rel in config.files:
            if config.files[rel]["sha256"] != current_sha:
                updates.append((rel, abs_path))
        else:
            adds.append((rel, abs_path))

    tracked_set = set(config.files.keys())
    local_set = set(local_files.keys())
    for rel in sorted(tracked_set - local_set):
        deletes.append(rel)

    total = len(adds) + len(updates) + len(deletes)
    log(f"Changes: {len(adds)} new, {len(updates)} updated, {len(deletes)} deleted, {total} total")

    if total == 0:
        log("Nothing to sync.")
        config.last_sync_commit = git_head_sha(str(Path(config.source_dir)))
        if not dry_run:
            save_manifest(config)
        return

    now = datetime.now(timezone.utc).isoformat()
    errors = 0

    # Process deletes first
    for rel in deletes:
        ov_uri = config.files[rel].get("ov_uri", file_to_ov_uri(rel, config.target_root))
        log(f"  DELETE {rel}  ->  rm {ov_uri}")
        if not dry_run:
            ok, out = ov_cmd(["rm", ov_uri, "--recursive"], port)
            if ok:
                del config.files[rel]
            else:
                log(f"    rm failed (may not exist): {out}", "WARN")
                del config.files[rel]

    # Process updates (rm then add)
    for rel, abs_path in updates:
        ov_uri = file_to_ov_uri(rel, config.target_root)
        parent_uri = file_to_parent_uri(rel, config.target_root)
        log(f"  UPDATE {rel}")

        if not dry_run:
            log(f"    rm {ov_uri}")
            ok, out = ov_cmd(["rm", ov_uri, "--recursive"], port)
            if not ok and "not found" not in out.lower():
                log(f"    rm warning: {out}", "WARN")

            if not ov_mkdir_chain(parent_uri, port):
                log(f"    Failed to create parent {parent_uri}", "ERROR")
                errors += 1
                continue

            log(f"    add -> {parent_uri}")
            ok, out = ov_cmd([
                "add-resource", str(abs_path),
                "--parent", parent_uri + "/",
                "--reason", f"sync-update {now}",
                "--wait", "--timeout", "120",
            ], port, timeout=150)
            if ok:
                config.files[rel] = {
                    "sha256": sha256_file(abs_path),
                    "ov_uri": ov_uri,
                    "synced_at": now,
                }
            else:
                log(f"    add failed: {out}", "ERROR")
                errors += 1

    # Process adds
    for rel, abs_path in adds:
        ov_uri = file_to_ov_uri(rel, config.target_root)
        parent_uri = file_to_parent_uri(rel, config.target_root)
        log(f"  NEW    {rel}")

        if not dry_run:
            if ov_uri_exists(ov_uri, port):
                log(f"    URI already exists, removing first: {ov_uri}", "WARN")
                ov_cmd(["rm", ov_uri, "--recursive"], port)

            if not ov_mkdir_chain(parent_uri, port):
                log(f"    Failed to create parent {parent_uri}", "ERROR")
                errors += 1
                continue

            log(f"    add -> {parent_uri}")
            ok, out = ov_cmd([
                "add-resource", str(abs_path),
                "--parent", parent_uri + "/",
                "--reason", f"sync-new {now}",
                "--wait", "--timeout", "120",
            ], port, timeout=150)
            if ok:
                config.files[rel] = {
                    "sha256": sha256_file(abs_path),
                    "ov_uri": ov_uri,
                    "synced_at": now,
                }
            else:
                log(f"    add failed: {out}", "ERROR")
                errors += 1

    # Final wait to drain processing queue
    if not dry_run:
        log("Waiting for OV processing to complete...")
        ov_cmd(["wait"], port, timeout=300)

    config.last_sync_commit = git_head_sha(str(Path(config.source_dir)))

    if not dry_run:
        save_manifest(config)

    log(f"Sync complete. {total - errors} succeeded, {errors} errors.")
    if errors > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_env()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("Commands:")
        print("  bootstrap <workspace> [--dry-run]   Nuke resources, full re-ingest")
        print("  sync <workspace> [--dry-run]        Incremental sync (changed files only)")
        print("  status <workspace>                  Show what would change (dry-run)")
        print()
        print(f"Settings directory: {SETTINGS_DIR}")
        print("Configured workspaces:")
        for ws in list_workspaces():
            settings = load_workspace_settings(ws)
            source = settings.get("source_dir", "(not configured)")
            port = settings.get("port", 0)
            include = ", ".join(settings.get("include", []))
            print(f"  {ws} (port {port}) -> {source}  [{include}]")
        sys.exit(0)

    command = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    # Get workspace name
    workspace = None
    for arg in sys.argv[2:]:
        if not arg.startswith("--"):
            workspace = arg
            break

    if not workspace:
        log("Workspace name required. Available:", "ERROR")
        for ws in list_workspaces():
            print(f"  {ws}")
        sys.exit(1)

    # Resolve workspace config from settings file
    settings = load_workspace_settings(workspace)
    if not settings:
        log(f"Unknown workspace: {workspace}", "ERROR")
        log(f"Create {SETTINGS_DIR / (workspace + '.json')} to configure it")
        sys.exit(1)

    port = settings.get("port", 0)
    if not port:
        log(f"No port configured for workspace '{workspace}'", "ERROR")
        sys.exit(1)

    source_dir = settings.get("source_dir", "")
    if not source_dir:
        log(f"No source_dir configured for workspace '{workspace}'", "ERROR")
        sys.exit(1)

    # Load sync state from manifest, apply settings
    config = load_manifest(workspace)
    config.workspace = workspace
    config.port = port
    config.source_dir = source_dir
    config.target_root = settings.get("target_root", "viking://resources")
    config.include = settings.get("include", ["*.md", "*.json"])

    if command == "status":
        cmd_status(config)
    elif command == "bootstrap":
        cmd_bootstrap(config, dry_run=dry_run)
    elif command == "sync":
        cmd_sync(config, dry_run=dry_run)
    else:
        log(f"Unknown command: {command}", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
