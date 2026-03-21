# Jura

A command-line Q&A tool that queries [OpenViking](https://openviking.dev)-indexed knowledge bases and synthesizes answers using Claude.

Jura is a **retrieval-only** system. It never uses LLM training data or general knowledge. If the answer isn't in your knowledge base, it says so. No hallucinations, no guessing.

## How It Works

```
Question
   ↓
Parallel Search (ov search + ov find)
   ↓
Merge, Deduplicate, Apply Recency Decay
   ↓
Read Top N Documents
   ↓
Claude Sonnet (retrieval-only prompt)
   ↓
Answer with Source Citations
```

1. **Dual search** — Runs `ov search` (context-aware retrieval) and `ov find` (pure semantic search) in parallel against your OpenViking server. Both respect a configurable score threshold.
2. **Recency decay** — Extracts dates from URI paths and applies a decay factor that halves relevance every `JURA_DECAY_DAYS` days (default: 10). The decay factor floors at 0.01, so old but highly relevant documents are never fully invisible. Documents without dates in their path (org docs, architecture files) are treated as evergreen — no penalty.
3. **Score-ranked reading** — Merges results from both searches, deduplicates by URI, filters out abstracts/overviews, re-ranks by decay-adjusted score, and reads the top N documents.
4. **Retrieval-only synthesis** — Sends the document context to Claude Sonnet with strict instructions: answer only from the provided documents, cite sources, and refuse to answer if the context doesn't contain the information.

## Installation

### Prerequisites

- **bash** 3.2+ (macOS default works)
- **[OpenViking](https://openviking.dev)** server running with indexed knowledge base
- **[ov CLI](https://openviking.dev)** — OpenViking command-line client
- **[claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — Anthropic's Claude Code CLI
- **jq** — JSON processor (`brew install jq`)
- **Python 3** — for sync engine, memory bridge, and MCP bridge
- **openviking** Python package — `pipx install openviking` (required for MCP bridge and hooks)

### Install

```bash
# Clone the repo
git clone <your-jura-repo-url> ~/path/to/jura
cd ~/path/to/jura

# Configure
cp .env.example .env
# Edit .env with your identity, model, and search tuning
# Edit .jura/<workspace>.json with your workspace configs

# Symlink to PATH (edits are immediately live)
ln -sf "$(pwd)/jura" ~/.local/bin/jura

# Verify
jura -h
```

See [SETUP.md](SETUP.md) for the full step-by-step guide.

## Usage

### One-shot Q&A

```bash
jura "who leads the Checkout squad?"
```

### Query a different workspace

```bash
jura -w 518 "what is buddy chat?"
```

### Session mode (multi-turn conversations)

```bash
# Daily session (resets at midnight)
jura -s "who leads the Checkout squad?"
jura -s "what repos do they own?"        # follow-up carries context

# Named session (persists across days)
jura -s research "what is the WIRE protocol?"
jura -s research "how does it handle auth?"
```

### Pipe input

```bash
echo "what is the WIRE protocol?" | jura
```

### Combine flags

```bash
jura -w 518 -s "what's the rent model?"
```

## Sync

Jura includes a built-in sync engine that keeps your local files indexed in OpenViking. It tracks every synced file's SHA256 hash and only re-ingests what changed.

```bash
# Check what would change (dry-run)
jura sync --status

# Incremental sync (only changed files)
jura sync

# Full re-ingest (nukes existing data, starts fresh)
jura sync --bootstrap

# Sync a specific workspace
jura sync -w 518

# Preview without executing
jura sync -w 518 --dry-run
```

The sync reads workspace config from `.jura/<workspace>.json` settings files. File discovery uses `git ls-files` (only git-tracked files are synced), then filters by the `include` patterns in the settings file. Sync state is stored per-workspace at `.openviking/manifests/<workspace>.json`.

### Typical workflow

```bash
# First time: bootstrap to do a full ingest
jura sync --bootstrap

# After making changes to your docs
jura sync

# Check what changed before syncing
jura sync --status
```

## Server Management

Start, stop, and check the status of your OpenViking servers:

```bash
jura serve                         # Start all workspace servers
jura serve -w my-workspace         # Start one server
jura serve --status                # Check what's running
jura serve --stop                  # Stop all servers
jura serve --stop -w 518           # Stop one server
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

Jura supports multiple OpenViking instances. Each workspace is defined by a settings file at `.jura/<workspace>.json`:

```json
{
  "port": 1934,
  "project_dir": "/path/to/your/project",
  "source_dir": "/path/to/your/project/docs",
  "target_root": "viking://resources",
  "include": ["*.md", "*.json"]
}
```

| Field | Description |
|-------|-------------|
| `port` | OpenViking server port for this workspace |
| `project_dir` | Project root (where `.openviking/ov.conf` lives). Used by `jura serve` |
| `source_dir` | Local directory to sync from |
| `target_root` | OV URI root for ingested files |
| `include` | File glob patterns to sync (matched against git-tracked files) |

The `-w` flag selects a workspace for Q&A, sync, and ls. No `-w` uses `JURA_DEFAULT_WORKSPACE` from `.env`.

To add a new workspace: create `.jura/<name>.json`, start an OV server on the configured port, then `jura sync -w <name> --bootstrap`.

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

The decay factor **floors at 0.01** (1%). A highly relevant old document (score 0.50) decays to at most 0.005, rather than vanishing to zero. This ensures important historical content remains findable.

**Evergreen documents** — URIs without a date pattern (org docs, architecture files, configs) — receive **no decay penalty**. They always compete at full score.

### Supported URI date patterns

| Pattern | Example | Extracted Date |
|---------|---------|---------------|
| `MM-YYYY/DD-to-DD` | `slack/03-2026/16-to-20/` | March 20, 2026 (end date) |
| `MM-YYYY/DD-to-DD` (month-crossing) | `linear/03-2026/30-to-03/` | April 3, 2026 (rolls to next month) |
| `DD_MM` in filename | `daily/03-2026/21_03.html` | March 21, 2026 |
| `MM_DD_week_plan` | `weekly_plans/03_09_week_plan.md` | March 9, 2026 |
| `MM-YYYY` only | `linear/03-2026/` | March 1, 2026 (conservative) |

For date ranges, the **end date** is used (most generous interpretation). When the end day is less than the start day (e.g. `30-to-03`), it's recognized as a month-crossing range and the month (and year, for December) is incremented. For month-only patterns, the **1st of the month** is used (most conservative — decays faster).

## Score Threshold

Results below the threshold are discarded before ranking. This prevents low-relevance documents from consuming read slots.

The threshold operates on OpenViking's similarity score (0.0 to 1.0). The default of `0.10` filters out only truly irrelevant noise while keeping real matches.

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

### Examples

```bash
# Broader search, more context
JURA_RESULTS=12 JURA_READ=8 jura "what happened in the last standup?"

# Stricter quality, fewer but better results
JURA_THRESHOLD=0.25 jura "what is the secret manager pattern?"

# Quick lookup, minimal context
JURA_READ=2 jura "who is Denis?"
```

## Output

### stderr (diagnostic, dimmed)

Every query prints ranked results to stderr so you can see what jura found and how it scored:

```
[my-workspace] Searching...
  Results (adjusted / raw / decay / age):
    0.4476 (raw:0.4476) [evergreen] viking://resources/docs/architecture.md
    0.3413 (raw:0.3413 x1.0000, 1d old) viking://resources/notes/03-2026/16-to-20/standup.md
    0.1496 (raw:0.2993 x0.5000, 6d old) viking://resources/notes/03-2026/09-to-13/retro.md
  Reading viking://resources/docs/architecture.md
  Reading viking://resources/notes/03-2026/16-to-20/standup.md
  Reading viking://resources/notes/03-2026/09-to-13/retro.md
  Thinking...
```

### stdout (the answer)

Clean, concise answer with source citations. Only from the provided context — never from general knowledge.

## Session Mode

Sessions enable multi-turn conversations where follow-up questions carry context from previous answers.

| Mode | Session ID | Lifetime |
|------|-----------|----------|
| `jura -s "question"` | `jura-YYYYMMDD` | Resets at midnight |
| `jura -s myname "question"` | `myname` | Persists until you stop using it |

Sessions are managed server-side by OpenViking (`ov search --session-id`). The session context makes follow-up searches more relevant — if you asked about "the Checkout squad" in your first question, a follow-up about "their repos" will bias search results toward Checkout-related documents.

## Architecture

```
  Local Files                         OpenViking Server
  (your docs)                         (port from .jura/)
       │                                  ↑       ↑
       │                                  │       │
  jura sync                          ov search  ov find
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

**Left side:** `jura sync` keeps OpenViking indexed. Compares local files against SHA256 manifests, only re-ingests what changed.

**Right side:** `jura "question"` queries the indexed data. Dual search, decay-ranked, retrieval-only answers.

### Why two search modes?

- **`ov search`** — Context-aware retrieval. Uses session history to understand what you're asking about. Better for follow-up questions and ambiguous queries.
- **`ov find`** — Pure semantic search. Matches by embedding similarity. Better for first-time, specific queries.

Running both in parallel and merging results gives broader coverage than either alone.

### Why bash?

- Zero dependencies beyond the CLI tools you already have
- macOS bash 3.2 compatible (no associative arrays, no bash 4+ features)
- ~350 lines — easy to read, modify, and debug
- Process substitution and background jobs (`&` + `wait`) for parallel execution
- Temp directory isolation for OpenViking config (avoids global config conflicts)

## OpenViking Plugin System

Beyond Q&A and sync, jura is the canonical home for shared OpenViking plugin infrastructure. These plugins provide MCP integration, session memory, and memory recall for any workspace that uses Claude Code with OpenViking.

### What's included

| Component | Path | Purpose |
|-----------|------|---------|
| MCP bridge | `.openviking/plugin/mcp_bridge.py` | Exposes `search()`, `read_resource()`, `list_memories()` as MCP tools |
| Hooks | `.openviking/plugin/hooks/` | Claude Code lifecycle hooks for session memory (start, stop, end, prompt) |
| Memory bridge | `.openviking/plugin/scripts/ov_memory.py` | Session management + long-term memory extraction via OpenViking |
| Browse | `.openviking/plugin/browse.py` | Interactive memory explorer CLI |

### How workspaces use it

Each consuming workspace references jura's plugin scripts via absolute paths in its own config files:

- **`.mcp.json`** — points to `jura/.openviking/plugin/mcp_bridge.py` with the workspace's port
- **`.claude/settings.local.json`** — hook commands point to `jura/.openviking/plugin/hooks/*.sh`
- **`memory-recall` skill** — calls `jura/.openviking/plugin/scripts/ov_memory.py`

Workspace-specific state stays local:
- `.openviking/ov.conf` — server port, storage path, API keys
- `.openviking/data-knowledgebases/` — vector DB + file storage
- `.openviking/session/` — runtime session state

### Path resolution

The hooks separate "where the code lives" from "which project is running":
- **`PLUGIN_ROOT`** — derived from the hook script's own location (always resolves to jura)
- **`PROJECT_DIR`** — derived from `$CLAUDE_PROJECT_DIR` (the workspace where Claude Code is running)

This means the same scripts work for any workspace without modification. See [SETUP.md](SETUP.md) for step-by-step workspace setup.

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
| v2.1 | 2026-03-21 | `jura sync` subcommand — incremental sync engine with per-workspace manifests |
| v3.0 | 2026-03-22 | Consolidated OV plugin system — MCP bridge, hooks, memory bridge as shared infrastructure |
| v3.1 | 2026-03-23 | Workspace settings files (`.jura/*.json`), git-based file discovery, `jura ls` command, 518 workspace bootstrap |
| v3.2 | 2026-03-23 | `jura serve` — start/stop/status for OV servers, `project_dir` in workspace settings, OpenViking upgraded to 0.2.9 |
| v3.3 | 2026-04-04 | MCP bridge: dynamic memory root discovery (fixes `viking://user/memories/` → `viking://user/default/memories/`). Recency decay: fix month-crossing date ranges (`30-to-03` parsed as April 3, not March 3). Decay half-life raised from 5 to 10 days, decay factor floors at 0.01 |

## License

Private tool. Not published.
