# Jura

**NEVER commit changes or ask/suggest to commit. The user handles all git operations.**

CLI Q&A tool, shared OpenViking plugin infrastructure, and backend services.
Q&A + sync + browse: bash script (~350 lines) + Python sync engine.
Plugins: MCP bridge, Claude Code hooks (session memory), memory recall bridge.
Apps: Management API (FastAPI + PostgreSQL, Docker Compose).

## Structure

```
jura                                    # Main CLI (bash, macOS bash 3.2 compatible)
.jura/<workspace>.json                  # Per-workspace settings (port, source_dir, include) — tracked
.claude/skills/epics-police/            # Epics Police skill (moved from apps/management-api/)
.env                                    # Runtime config only (gitignored)
.env.example                            # Documented config template
.openviking/plugin/mcp_bridge.py        # MCP server (OV search/read/list, port parameterized)
.openviking/plugin/hooks/               # Claude Code lifecycle hooks (session memory)
.openviking/plugin/scripts/ov_memory.py # Session + long-term memory bridge (Python 3)
.openviking/plugin/scripts/ov_sync.py   # Sync engine (Python 3, git ls-files based)
.openviking/plugin/browse.py            # Interactive memory explorer
.openviking/manifests/                  # Per-workspace sync state (gitignored)
apps/management-api/                    # FastAPI + PostgreSQL backend (Docker Compose)
apps/management-api/static/             # Bundled UI files (epics-police.html, volume-mounted)
```

Plugins are shared infrastructure. Each consuming workspace
points its `.mcp.json` and `.claude/settings.local.json` hooks to jura's plugin directory.
Workspace-specific config (`.openviking/ov.conf`, data, session state) stays per-project.

## Commands

```bash
# Q&A
./jura "your question"
./jura -w <workspace> "question"
./jura -s "session question"

# OpenViking
./jura ov status               # Show server status
./jura ov restart              # Start or restart all OV servers
./jura ov stop                 # Stop all servers
./jura ov logs                 # Last 100 log lines
./jura ov sync                 # Incremental sync (default workspace)
./jura ov sync --bootstrap     # Full re-ingest
./jura ov sync --status        # Dry-run
./jura ov sync -w <workspace>  # Sync a specific workspace

# Browse
jura ls                        # List OV resources (default workspace)
jura ls -w <workspace>         # List resources for a workspace
jura ls viking://resources/docs/  # Drill into a URI path

# Management API
jura api status                # Health check + weeks data summary
jura api restart               # Start or restart API + DB (data preserved)
jura api stop                  # Stop containers (data preserved)
jura api logs                  # Last 100 API log lines
jura api job start             # Start periodic fetch every 20 min (launchd)
jura api job start 600         # Custom interval (seconds)
jura api job stop              # Stop periodic fetch
jura api job status            # Check if job is active
```

## Workspace Configuration

Workspaces are defined in `.jura/<workspace>.json` (tracked in git):

```json
{
  "port": 1934,
  "project_dir": "/path/to/project",
  "source_dir": "/path/to/project/docs",
  "target_root": "viking://resources",
  "include": ["*.md", "*.json"]
}
```

- `port` — OpenViking server port for this workspace
- `project_dir` — project root (where `.openviking/ov.conf` lives). Used by `jura serve`
- `source_dir` — local directory to sync from
- `include` — file patterns to sync (matched against git-tracked files)
- `target_root` — OV URI root for ingested files

Runtime config (`.env`): `JURA_DEFAULT_WORKSPACE`, `JURA_NAME`, `JURA_IDENTITY`, `JURA_MODEL`, search tuning vars (`JURA_RESULTS`, `JURA_READ`, `JURA_THRESHOLD`, `JURA_DECAY_DAYS`).

## Management API

REST API at `apps/management-api/` — centralizes Slack, Linear, Fireflies, and Notion data into PostgreSQL with week-based storage. See `apps/management-api/README.md` for full docs.

- **Docker**: `jura api restart` (API on `:8100`, DB on `:5433`, migrations auto-run)
- **Local dev**: `cd apps/management-api && bash scripts/setup-local.sh` (venv, deps, DB, migrations, test DB)
- **Test**: `pytest tests/ -v` (70 tests: unit + e2e)
- **Docs**: `http://localhost:8100/docs` (auto-generated OpenAPI)
- **Bruno**: Open `apps/management-api/bruno/` folder in Bruno, select "Local" env
- **Backup**: `./backup_db.sh` (pg_dump to `backups/`)

Database schema managed by Alembic (3 migrations, 12 tables). In Docker, migrations run automatically on container startup. For local dev, `setup-local.sh` handles it, or run `alembic upgrade head` manually.

Key endpoints: `POST /api/v1/{slack,linear,meets,epics}/fetch?week=YYYY-MM-DD` to fetch, `GET /api/v1/{source}?week=...` to read. Week param snaps any date to Monday-Sunday. Linear write: `PATCH /api/v1/linear/tickets/{id}` (reparent, set children, update fields), `POST /api/v1/linear/tickets` (create). Epics Police: `GET/POST /api/v1/epics-police/analysis`, `POST/GET /api/v1/epics-police/decisions` (feedback loop), `GET /api/v1/epics-police/learnings` (distilled weights), `POST /api/v1/epics-police/distill` (recompute). Interactive UI: `GET /epics-police`.

`.env` holds API keys (SLACK_BOT_TOKEN, LINEAR_API_KEY, FIREFLIES_API_KEY, NOTION_API_KEY). All behavioral config (watched channels, DM people, team names, exclusion rules) in the `configs` DB table — must be seeded after first startup. See `apps/management-api/README.md` "Required Config" section for curl commands.

### Skills

The **Epics Police** skill lives at `.claude/skills/epics-police/`. Invoke with `/epics-police` from the repo root. It fetches Linear/epics/people from the API, runs deterministic + LLM matching, pushes analysis to `POST /api/v1/epics-police/analysis`, and opens `http://localhost:8100/epics-police`. The interactive UI is bundled at `apps/management-api/static/epics-police.html` and served by the API (volume-mounted for live editing). No local file output.

The skill has a **self-improving feedback loop**: the UI logs accept/reject/redirect decisions (with full signal context) to `POST /api/v1/epics-police/decisions`. On each run, the skill fetches learned weights from `GET /api/v1/epics-police/learnings`, uses them for scoring instead of defaults, detects implicit decisions by comparing previous analysis to current Linear state, triggers `POST /api/v1/epics-police/distill` to recompute learnings, and updates its own SKILL.md tunables block when learned weights diverge >5% from current values. Distillation uses Bayesian weight updating with exponential decay (8-week half-life), confidence calibration per band, and structural pattern detection (cross-squad redirects, label false positives, epic over-suggestion).

## Dependencies

- bash 3.2+ (macOS default)
- `ov` CLI (OpenViking)
- `claude` CLI (Anthropic Claude Code)
- `jq` (JSON processor)
- Python 3 (sync engine, memory bridge, MCP bridge)
- `openviking` Python package (MCP bridge + memory hooks; installed via pipx)

## Key Patterns

- Config loaded from `.env` via `source` at script start; follows symlinks to find `.env` relative to the real script location
- Workspace port/source resolved from `.jura/<workspace>.json` settings files via `jq`
- Parallel dual search: `ov search` (context-aware) + `ov find` (semantic) run as background jobs with `&` + `wait`
- `$HOME` override trick: creates temp dir with `ovcli.conf` so `ov` CLI targets the right workspace port (the CLI ignores `OV_URL` env var)
- Recency decay: halves score every `JURA_DECAY_DAYS` days (default 10); decay factor floors at 0.01; URIs without dates are treated as evergreen
- Date ranges that cross month boundaries (e.g. `30-to-03`) are detected and the month is incremented for the end date
- Sync discovers files via `git ls-files` (automatically excludes node_modules, dist, etc.), then filters by `include` patterns from workspace settings
- Manifests at `.openviking/manifests/<workspace>.json` store only sync state (last_sync_commit + file hashes)
- MCP bridge discovers memory roots dynamically by listing `viking://user/` and `viking://agent/`, then appending `/memories/` to each entry (e.g. `viking://user/default/memories/`, `viking://agent/<id>/memories/`). Never hardcode memory paths — OV uses intermediate namespace directories
- **Plugin separation**: hooks derive `PLUGIN_ROOT` from `${BASH_SOURCE[0]}` (jura's scripts dir) and `PROJECT_DIR` from `$CLAUDE_PROJECT_DIR` (the consuming workspace). Consuming projects reference jura's hooks via absolute paths in their `.claude/settings.local.json`, while project-specific config (`.openviking/ov.conf`, session state) stays local

## Gotchas

- `date -j -f` is macOS-specific; Linux would need `date -d` adaptation
- No bash 4+ features (no associative arrays) — intentional for macOS compatibility
- `$HOME` is temporarily overridden during OV calls and restored before calling `claude`
- `.env` is gitignored and required for operation — copy from `.env.example`
- The `jura` file has no extension but is a bash script (shebang: `#!/usr/bin/env bash`)
- The `ov` CLI ignores `OV_URL` env var — always use the `$HOME` override trick to target a specific port
- MCP bridge and hooks require the `openviking` Python package (installed via pipx at `~/.local/pipx/venvs/openviking/`)
- Consuming workspaces use absolute paths to jura's plugin scripts — if jura repo moves, update those references

## Code Style

- Bash: `set -euo pipefail`, double-quoted variables, `${VAR:-default}` for defaults
- Comments use `# ─── Section ───` box style for major sections
- stderr for diagnostics (dimmed ANSI), stdout for the answer only
- Python sync script: standalone, invoked via `exec python3`
