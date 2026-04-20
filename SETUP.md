# Setup Guide

This guide walks you through setting up Jura from scratch on a new machine or for a new knowledge base.

## Prerequisites

Install these before proceeding:

```bash
# OpenViking server + CLI (https://openviking.dev)
ov --version

# Claude Code CLI (https://docs.anthropic.com/en/docs/claude-code)
claude --version

# jq JSON processor
brew install jq              # macOS
# sudo apt-get install jq    # Linux

# Python 3 (sync engine, MCP bridge, memory hooks)
python3 --version

# OpenViking Python package (MCP bridge + hooks)
pipx install openviking
```

You also need at least one OpenViking server running with an indexed knowledge base.

## Step 1: Clone the Repository

```bash
git clone <your-jura-repo-url> ~/path/to/jura
cd ~/path/to/jura
```

## Step 2: Configure Your Environment

### Workspace settings

Create a settings file for each workspace at `.jura/<workspace>.json`:

```json
{
  "port": 1934,
  "source_dir": "/path/to/your/docs",
  "target_root": "viking://resources",
  "include": ["*.md", "*.json"]
}
```

- `port` — must match the port your OpenViking server is listening on
- `source_dir` — local directory containing the files to sync
- `include` — file patterns to sync (matched against git-tracked files only)

Verify the OV server is reachable:

```bash
jura ls -w my-project   # Should list indexed resources (or empty if first time)
```

### Runtime config

Copy the example config and edit it:

```bash
cp .env.example .env
```

Set the default workspace:

```bash
JURA_DEFAULT_WORKSPACE="my-project"
```

### Identity

Customize who the assistant is. This text goes into the system prompt sent to Claude.

```bash
JURA_NAME="Atlas"
JURA_IDENTITY="the engineering team's knowledge assistant"
```

The system prompt becomes: *"You are Atlas, the engineering team's knowledge assistant. You are a RETRIEVAL-ONLY system..."*

### Model

Choose which Claude model synthesizes answers.

```bash
JURA_MODEL="sonnet"    # Fast, good quality (recommended)
JURA_MODEL="opus"      # Slower, highest quality
JURA_MODEL="haiku"     # Fastest, lower quality
```

### Search Tuning

```bash
JURA_RESULTS=8       # Raw candidates per search command (2 run in parallel = 16 max)
JURA_READ=5          # Documents to read and send to Claude
JURA_THRESHOLD=0.10  # Minimum similarity score (0.0–1.0)
```

**Trade-offs:**
- Higher `JURA_RESULTS` = wider search funnel, slightly slower
- Higher `JURA_READ` = richer context for Claude, more tokens/cost
- Higher `JURA_THRESHOLD` = fewer but more relevant results (too high = no results)

### Recency Decay

```bash
JURA_DECAY_DAYS=5    # Halve relevance every N days
JURA_DECAY_DAYS=7    # Weekly decay (gentler)
JURA_DECAY_DAYS=0    # Disable decay entirely
```

Decay only applies to documents whose URI path contains a date pattern. Documents without dates (org docs, architecture files, configs) are treated as evergreen and never penalized.

**Supported date patterns in URI paths:**

| Pattern | Example | How It's Parsed |
|---------|---------|-----------------|
| `MM-YYYY/DD-to-DD` | `slack/03-2026/16-to-20/` | End date used (March 20) |
| `DD_MM` in filename | `daily/21_03.html` | March 21 |
| `MM_DD_week_plan` | `03_09_week_plan.md` | March 9 |
| `MM-YYYY` (month only) | `data/03-2026/` | 1st of month (March 1) |

If your knowledge base uses different date conventions in its paths, you'll need to add patterns to the `uri_epoch()` function in the `jura` script.

## Step 3: Install to PATH

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/jura" ~/.local/bin/jura
```

If `~/.local/bin` isn't in your PATH, add `export PATH="$HOME/.local/bin:$PATH"` to your shell rc file.

## Step 4: Verify the Setup

### Check prerequisites

```bash
command -v ov     && echo "ov: OK"      || echo "ov: MISSING"
command -v claude && echo "claude: OK"  || echo "claude: MISSING"
command -v jq     && echo "jq: OK"      || echo "jq: MISSING"
command -v jura   && echo "jura: OK"    || echo "jura: MISSING"
```

### Check OpenViking server is running

```bash
jura ls -w <workspace> viking://resources/
```

You should see a list of indexed resources. If you get a connection error, start your OpenViking server first (`jura ov restart -w <workspace>`).

> The raw `ov` CLI ignores `OV_URL` — it only reads `~/.openviking/ovcli.conf`. Use `jura ls` (which sets the per-workspace port via a scoped `$HOME`) or run `ov` with a temp `$HOME` pointing at a `ovcli.conf` with the right URL.

### Run a test query

```bash
jura -h                          # Should show help with your configured workspaces
jura "what documents are indexed" # Should return an answer from your knowledge base
```

### Check scoring output

The stderr output shows you exactly what Jura found and how it ranked:

```
[my-project] Searching...
  Results (adjusted / raw / decay / age):
    0.4476 (raw:0.4476) [evergreen] viking://resources/architecture.md
    0.3413 (raw:0.3413 x1.0000, 1d old) viking://resources/notes/03-2026/16-to-20/standup.md
  Reading viking://resources/architecture.md
  Reading viking://resources/notes/03-2026/16-to-20/standup.md
  Thinking...
```

If results say `[evergreen]` for everything, your URI paths may not match the expected date patterns. Check the **Recency Decay** section above.

## Step 5: Sync Your Knowledge Base

Before Jura can answer questions, your local files need to be indexed in OpenViking. The sync engine uses `git ls-files` to discover tracked files, filters by the `include` patterns in your workspace settings (`.jura/<workspace>.json`), and only re-ingests what changed.

Sync configuration comes from the workspace settings file you created in Step 2. The `source_dir` and `include` fields control what gets synced. No additional `.env` configuration is needed.

### First sync (bootstrap)

Bootstrap nukes all existing resources in OV and does a full re-ingest. Run this once per workspace:

```bash
# Full re-ingest (nukes existing data in OV, starts fresh)
jura ov sync -w my-project --bootstrap

# Preview what will happen first
jura ov sync -w my-project --bootstrap --dry-run
```

This creates a manifest file at `.openviking/manifests/<workspace>.json` tracking every synced file's SHA256 hash.

### Incremental sync

After the initial bootstrap, use `jura ov sync` to only re-ingest changed files:

```bash
jura ov sync -w my-project
```

The sync compares each local file's SHA256 against the manifest. Only files with different hashes are re-ingested. Deleted files are removed from OV.

### Check sync status

See what would change without doing anything:

```bash
jura ov sync -w my-project --status
```

Output shows NEW, UPDATE, DELETE, and SKIP counts with file-level detail.

### Typical workflow

```bash
# First time: full ingest
jura ov sync -w my-project --bootstrap

# After making changes to your docs:
jura ov sync -w my-project

# Quick check before syncing:
jura ov sync -w my-project --status
```

## Step 6: Connect a Workspace to Jura's Plugins (Optional)

Jura also provides shared OpenViking plugin infrastructure — an MCP bridge, Claude Code lifecycle hooks (session memory), and a memory recall bridge. Consuming workspaces reference jura's scripts via absolute paths so the tooling lives in one place.

To enable the plugin system for a workspace (e.g., `my-project`):

### 1. Ensure the workspace has an OpenViking config

Create `.openviking/ov.conf` in the workspace root with its server settings:

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "sk-...",
      "model": "text-embedding-3-small",
      "dimension": 1536
    }
  },
  "storage": {
    "workspace": "/path/to/my-project/.openviking/data-knowledgebases"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 1934
  }
}
```

### 2. Add MCP bridge

Create `.mcp.json` in the workspace root, pointing to jura's bridge with the workspace port:

```json
{
  "mcpServers": {
    "openviking": {
      "type": "stdio",
      "command": "/Users/<you>/.local/pipx/venvs/openviking/bin/python3",
      "args": [
        "/path/to/jura/.openviking/plugin/mcp_bridge.py",
        "1934"
      ]
    }
  }
}
```

### 3. Add Claude Code hooks

In `.claude/settings.local.json`, add hooks that point to jura's hook scripts:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "bash /path/to/jura/.openviking/plugin/hooks/session-start.sh", "timeout": 12 }] }
    ],
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "bash /path/to/jura/.openviking/plugin/hooks/user-prompt-submit.sh", "timeout": 8 }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "bash /path/to/jura/.openviking/plugin/hooks/stop.sh", "async": true, "timeout": 120 }] }
    ],
    "SessionEnd": [
      { "hooks": [{ "type": "command", "command": "bash /path/to/jura/.openviking/plugin/hooks/session-end.sh", "timeout": 120, "async": true }] }
    ]
  }
}
```

### 4. Add memory-recall skill (optional)

Copy `.claude/skills/memory-recall/SKILL.md` from an existing workspace. Update the `BRIDGE` path to point to jura's `ov_memory.py`.

### How it works

The hooks resolve two paths independently:
- **`PLUGIN_ROOT`** — derived from the hook script location (jura), used to find `ov_memory.py`
- **`PROJECT_DIR`** — derived from `$CLAUDE_PROJECT_DIR` (the consuming workspace), used to find `ov.conf` and session state

This means the same hook scripts work for any workspace without modification.

## Troubleshooting

### "No results found"

1. **Threshold too high** — Try `JURA_THRESHOLD=0.05 jura "your question"` to see if results exist below the cutoff.
2. **OV server not running** — Check `jura ov status` (or `jura ls -w <workspace> viking://resources/`).
3. **Nothing indexed** — Run `jura ls -w <workspace> viking://resources/` to see what's in the knowledge base.

### "Could not read any documents"

The search found URIs but `ov read` failed. Usually means the OV server restarted between search and read. Re-run the query.

### Wrong workspace

If Jura is hitting the wrong knowledge base, check:
1. Your `.env` workspace mappings are correct
2. The default workspace is set correctly
3. You're passing `-w <name>` if needed

### Decay seems wrong

- Check `jura -h` to see the current `JURA_DECAY_DAYS` value
- Set `JURA_DECAY_DAYS=0` in `.env` to disable decay and compare results
- The stderr output shows the decay factor and age for each result — use this to debug

### macOS vs Linux

The `date -j -f` syntax in `uri_epoch()` is macOS-specific. On Linux, replace with:

```bash
# macOS (current)
date -j -f "%Y-%m-%d" "${y}-${m}-${d}" +%s

# Linux equivalent
date -d "${y}-${m}-${d}" +%s
```
