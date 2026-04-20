# Jura

Named after my dog — by day she's chasing birds in the garden, by night she helps me work with 2 dozen's engineering team.

Jura is a CLI toolkit for engineers who lead. It searches your knowledge bases, tracks what your team shipped, and keeps you honest about what's actually happening — not what you think is happening.

**The Q&A side** searches [OpenViking](https://openviking.dev)-indexed docs using Claude. It's retrieval-only: no training data, no hallucinations. If the answer isn't in your docs, she'll tell you. Jura doesn't guess.

**The management side** pulls Slack, Linear, Fireflies, and Notion into one place so you can ask "what happened this week" and get a real answer, not a status meeting.

```bash
$ jura "who's blocked on the server migration?"

[my-workspace] Searching...
  Results (adjusted / raw / decay / age):
    0.4476 (raw:0.4476) [evergreen] viking://resources/docs/checkout-migration.md
    0.3413 (raw:0.3413 x1.0000, 1d old) viking://resources/slack/04-2026/07-to-11/dev-general.md
  Reading 2 documents...
  Thinking...

Based on the Slack thread from April 9th, Alice is blocked on the payment
provider API change — waiting on the external team to ship their v2 endpoint.
(Source: slack/04-2026/07-to-11/dev-general.md)
```

### At a glance

| Command | What it does |
|---------|-------------|
| `jura "question"` | Search your docs, get a sourced answer |
| `jura ov sync` | Keep local files indexed in OpenViking |
| `jura ov api-sync` | Sync Management API data (Slack, Linear, Meets, Epics) into OpenViking |
| `jura ov restart` | Manage OpenViking servers |
| `jura api restart` | Run the team activity backend (Slack + Linear + Meets + Notion) |

## Installation

See [SETUP.md](SETUP.md) for the full step-by-step guide. Quick version:

```bash
git clone <your-jura-repo-url> ~/jura && cd ~/jura
cp .env.example .env                    # Edit with your config
ln -sf "$(pwd)/jura" ~/.local/bin/jura  # Add to PATH
jura -h                                 # Verify
```

Requires: bash 3.2+, [OpenViking](https://openviking.dev) (server + CLI), [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), jq, Python 3, `pipx install openviking`.

## Usage

### One-shot Q&A

```bash
jura "who leads the thunder squad?"
```

### Query a different workspace

```bash
jura -w 518 "what is buddy chat?"
```

### Session mode (multi-turn conversations)

```bash
# Daily session (resets at midnight)
jura -s "who leads the thunder squad?"
jura -s "what repos do they own?"        # follow-up carries context

# Named session (persists across days)
jura -s research "what is the stack protocol?"
jura -s research "how does it handle auth?"
```

### Pipe input

```bash
echo "what is the stack protocol?" | jura
```

### Combine flags

```bash
jura -w 518 -s "what's the rent model?"
```

## Sync

Jura includes a built-in sync engine that keeps your local files indexed in OpenViking. It tracks every synced file's SHA256 hash and only re-ingests what changed.

```bash
# Check what would change (dry-run)
jura ov sync --status

# Incremental sync (only changed files)
jura ov sync

# Full re-ingest (nukes existing data, starts fresh)
jura ov sync --bootstrap

# Sync a specific workspace
jura ov sync -w 518

# Preview without executing
jura ov sync -w 518 --dry-run
```

File discovery uses `git ls-files`, filtered by `include` patterns from `.jura/<workspace>.json`. Sync state stored at `.openviking/manifests/<workspace>.json`.

## API Sync

Sync pre-formatted data from the Management API directly into OpenViking's vector DB. The API renders Slack messages, Linear tickets, meetings, and epics as markdown — this command fetches that formatted content and ingests it into OV, scoped by week.

```bash
# Show what's in OV vs what's available from the API
jura ov api-sync --status -w command-center

# Sync current week (all sources)
jura ov api-sync -w command-center

# Sync a specific week
jura ov api-sync -w command-center --week 2026-04-06

# Sync one source only
jura ov api-sync -w command-center --source slack
```

Each run does a clean nuke + re-ingest for the target week+source. Running 100 times on the same week produces the same result — no duplicates. Staging files are written to `.openviking/staging/<workspace>/` (gitignored) and persist between runs for inspection; cleaned at the start of the next run.

Schedule it to run daily via launchd:

```bash
# Start daily job at 9 PM (default)
jura ov job start

# Daily at 6 AM
jura ov job start 6

# Check status / stop
jura ov job status
jura ov job stop
jura ov job logs
```

First arg to `start` is the hour (0-23, default 21). Each `start` unloads any existing job first — no duplicate jobs. Logs go to `.openviking/staging/ov-job.log`.

Requires `api_url` in the workspace config (`.jura/<workspace>.json`):

```json
{
  "port": 1934,
  "api_url": "http://localhost:8100",
  ...
}
```

Data lands in OV at `viking://resources/api/{week}/{source}/{slug}`:

```
viking://resources/api/2026-04-06/
├── slack/          (59 channel files)
├── linear/         (497 ticket files)
├── meets/          (27 meeting files)
└── epics/          (43 epic files)
```

## OpenViking Servers

```bash
jura ov status                     # Show server status
jura ov restart                    # Start or restart all servers
jura ov restart -w my-workspace    # Restart one server
jura ov stop                       # Stop all servers
jura ov logs                       # Last 100 log lines
jura ov logs -w my-workspace       # Logs for one server
```

Logs are written to `/tmp/ov-<workspace>.log`.

## Browse

List what's indexed in a workspace's OpenViking database:

```bash
jura ls                             # Default workspace
jura ls -w 518                      # Specific workspace
jura ls viking://resources/docs/    # Drill into a URI path
jura ls -w 518 viking://resources/apps/
```

## Management API

Jura wraps a FastAPI + PostgreSQL backend that centralizes team activity data from Slack, Linear, Fireflies, and Notion. The API runs in Docker Compose at `apps/management-api/`.

```bash
jura api status              # Health check + weeks data summary
jura api restart             # Start or restart (data preserved across restarts)
jura api stop                # Stop containers (data preserved)
jura api logs                # Last 100 API log lines
jura api job start           # Start periodic fetch every 20 min (launchd)
jura api job start 600       # Custom interval in seconds
jura api job stop            # Stop periodic fetch
jura api job status          # Check if job is active
```

The periodic job uses macOS launchd to fetch Slack, Linear, and Meets sequentially every 20 minutes (configurable). Notion epics are excluded (push-only). The job survives terminal closes and Mac restarts.

The API itself runs on `http://localhost:8100` with auto-generated docs at `http://localhost:8100/docs`. Data is fetched via POST endpoints and read via GET endpoints, all scoped to Monday-Sunday weeks. See `apps/management-api/README.md` for full API reference.

## Workspaces

Each workspace is a `.jura/<name>.json` file mapping to an OpenViking instance (port, source dir, file patterns). Use `-w <name>` to target a workspace, or set `JURA_DEFAULT_WORKSPACE` in `.env`. See `.jura/example.json.template` for the format. Add `"api_url"` to enable `jura ov api-sync` for that workspace.

## Recency Decay

Not all knowledge ages equally. A Slack message from 3 days ago is likely more relevant than one from 3 weeks ago. But your org chart is timeless.

Jura extracts dates from URI paths and applies a **halving decay every 10 days** (configurable via `JURA_DECAY_DAYS`):

| Age | Decay Factor | Effect |
|-----|-------------|--------|
| 0–9 days | 1.0 | Full relevance |
| 10–19 days | 0.5 | Half relevance |
| 20–29 days | 0.25 | Quarter relevance |
| 30–39 days | 0.125 | Eighth relevance |
| 60+ days | 0.01 (floor) | Minimum — never fully invisible |

URIs without dates (org docs, architecture files) are treated as **evergreen** — no decay. The decay factor floors at 0.01 so old documents never fully vanish.

## Environment Variables

Runtime configuration lives in `.env` at the repo root (gitignored). Workspace config lives in `.jura/<workspace>.json` (tracked). See `.env.example` for a documented template.

| Variable | Default | Description |
|----------|---------|-------------|
| `JURA_DEFAULT_WORKSPACE` | `default` | Workspace used when no `-w` flag is given |
| `JURA_NAME` | `Jura` | Assistant name in system prompt and session IDs |
| `JURA_IDENTITY` | `a fast Q&A assistant` | One-line personality for the system prompt |
| `JURA_MODEL` | `sonnet` | Claude model for answer synthesis |
| `JURA_RESULTS` | `8` | Results to request per search command (up to 16 raw candidates before dedup) |
| `JURA_READ` | `5` | Top-ranked documents to read and send to Claude as context |
| `JURA_THRESHOLD` | `0.10` | Minimum similarity score (0.0–1.0). Results below this are discarded |
| `JURA_DECAY_DAYS` | `10` | Days per decay half-life (floor at 0.01). Set to `0` to disable decay |

All variables can be overridden per-query: `JURA_READ=2 jura "who owns the payments service?"`

## Architecture

```
  Local Files                         OpenViking Server
  (your docs)                         (port from .jura/)
       │                                  ↑       ↑
       │                                  │       │
  jura ov sync                          ov search  ov find
  (ov_sync.py)                       (context) (semantic)
       │                                  │       │
  SHA256 diff ──── ov add-resource ──→    └──┬────┘
  per-workspace                              │
  manifests                           Merge + Dedup
                                             │
                                      Recency Decay
                                   (halve per 10d, floor 0.01)
                                             │
                                      Score Re-rank
                                             │
                                     Read Top N Docs
                                             │
                                   Claude Sonnet (-p)
                                  (retrieval-only prompt)
                                             │
                                          Answer
```

**Left side:** `jura ov sync` keeps OpenViking indexed from local files. `jura ov api-sync` does the same from Management API data (Slack, Linear, Meets, Epics) — fetches formatted markdown, writes to staging, nuke + re-ingest per week+source.

**Right side:** `jura "question"` queries the indexed data. Dual search, decay-ranked, retrieval-only answers.

Dual search (`ov search` + `ov find`) runs in parallel — context-aware retrieval plus pure semantic matching — then merges and deduplicates.

## Plugins

Jura also ships shared OpenViking plugins: MCP bridge, Claude Code session memory hooks, and a memory recall bridge. Any workspace can use them by pointing its `.mcp.json` and `.claude/settings.local.json` to jura's plugin scripts. See [SETUP.md](SETUP.md) step 6 for setup.

## Limitations

- **Structured data queries** — Jura excels at semantic questions ("what did Sam say about Instagram?") but struggles with filtering queries ("list all tickets assigned to Joseph"). This is a semantic search limitation — the knowledge base organizes data by topic/status, not by person.
- **macOS date command** — The `date -j -f` syntax is macOS-specific. On Linux, the `uri_epoch()` function would need adaptation to use `date -d`.
- **Score scale** — OpenViking scores are 0.0–1.0. Don't set `JURA_THRESHOLD` above 1.0 — it will filter everything.
- **Token cost** — Each query reads up to `JURA_READ` full documents and sends them to Claude. Large documents mean more tokens. Tune `JURA_READ` to balance answer quality vs. cost.

## Development History

| Version | Date | Changes |
|---------|------|---------|
| v0.1 | 2026-03-19 | Initial creation — single OV search, read top 3, pass to Claude |
| v0.2 | 2026-03-19 | Parallel dual search (`ov search` + `ov find`), result deduplication |
| v0.3 | 2026-03-19 | Retrieval-only system prompt |
| v0.7 | 2026-03-19 | Multi-workspace support (`-w` flag), workspace-to-port mapping |
| v0.8 | 2026-03-19 | Fixed document reading bug (`grep -v` instead of `tail -n +2`) |
| v0.9 | 2026-03-19 | Session mode (`-s` flag), help text, all features working |
| v1.0 | 2026-03-21 | Score threshold (`-t` flag to OV), bumped defaults (N_SEARCH=8, N_READ=5) |
| v1.1 | 2026-03-21 | Recency decay (halve every 5 days), score display in stderr, evergreen detection |
| v2.0 | 2026-03-21 | Own repository, all config externalized to `.env`, symlink-based install |
| v2.1 | 2026-03-21 | `jura ov sync` subcommand — incremental sync engine with per-workspace manifests |
| v3.0 | 2026-03-22 | Consolidated OV plugin system — MCP bridge, hooks, memory bridge as shared infrastructure |
| v3.1 | 2026-03-23 | Workspace settings files (`.jura/*.json`), git-based file discovery, `jura ls` command, 518 workspace bootstrap |
| v3.2 | 2026-03-23 | `jura serve` — start/stop/status for OV servers, `project_dir` in workspace settings, OpenViking upgraded to 0.2.9 |
| v3.3 | 2026-04-04 | MCP bridge: dynamic memory root discovery (fixes `viking://user/memories/` → `viking://user/default/memories/`). Recency decay: fix month-crossing date ranges (`30-to-03` parsed as April 3, not March 3). Decay half-life raised from 5 to 10 days, decay factor floors at 0.01 |
| v3.4 | 2026-04-12 | `jura ov api-sync` — sync Management API formatted data (Slack, Linear, Meets, Epics) into OV vector DB. Nuke + re-ingest per week+source, parallel fetch, staging dir. Fixed `ov` subcommand parser to pass through subcommand-specific flags |
| v3.5 | 2026-04-20 | Upgraded OpenViking 0.2.15 → 0.3.9. Memory V2 (YAML-templated memories under `entities/`, `events/`, `patterns/`, etc.) is now the active format; MCP bridge's dynamic root discovery already handles the new paths. Existing vector collections are auto-backfilled with embedding metadata on first boot — no reingest required |

## License

MIT
