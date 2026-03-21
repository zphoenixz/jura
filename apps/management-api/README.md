# Management API

RESTful API that centralizes team activity data from Slack, Linear, Fireflies, and Notion into a PostgreSQL database with week-based storage.

## Setup

### Option A: Docker (recommended)

Everything runs in containers. Migrations run automatically on startup.

```bash
cp .env.example .env
# Edit .env with your API keys

jura api restart             # Start API + Postgres (migrations auto-run)
jura api status              # Verify health
```

API: `http://localhost:8100` | Docs: `http://localhost:8100/docs` | DB: `localhost:5433`

### Option B: Local Development

API runs natively, only PostgreSQL in Docker. Hot-reload with `--reload`.

```bash
cd apps/management-api
bash scripts/setup-local.sh
```

The script handles:
1. Creates `.env` from `.env.example` (if missing)
2. Creates Python venv and installs dependencies
3. Starts PostgreSQL in Docker (port 5433)
4. Runs Alembic migrations (creates all 11 tables)
5. Creates the `management_test` database for tests

Then start the API:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8100
```

### Option C: Manual Setup

If you prefer to control each step:

```bash
# 1. Environment
cp .env.example .env           # Edit with your API keys

# 2. Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Database
docker compose up -d db        # PostgreSQL on port 5433

# 4. Migrations
alembic upgrade head           # Creates all tables

# 5. Test database
docker compose exec db psql -U mgmt -d management -c "CREATE DATABASE management_test"

# 6. Start API
uvicorn app.main:app --reload --port 8100

# 7. Run tests
pytest tests/ -v
```

## Database Schema

Alembic manages the schema. One migration (`alembic/versions/001_initial_tables.py`) creates all 11 tables.

**In Docker**: migrations run automatically on container startup (Dockerfile runs `alembic upgrade head` before uvicorn).

**Local dev**: run `alembic upgrade head` manually after starting the DB. The `DATABASE_URL` in `.env.example` already points to `localhost:5433` for local use. Docker Compose overrides this with its own `DATABASE_URL` pointing to the `db` container hostname.

### Tables

| Table | Purpose |
|-------|---------|
| `weeks` | Week anchor (monday_date, unique) |
| `configs` | Runtime config (source + key + JSONB value) |
| `persons` | Cross-integration identity (email, slack_user_id, linear_user_id, fireflies_name) |
| `fetch_logs` | Audit trail: when each source was fetched per week |
| `slack_messages` | Messages with channel_id, slack_ts, thread_ts, person FK |
| `linear_tickets` | Tickets with cycle info, status, labels, points, parent/child relationships |
| `linear_comments` | Comments FK to tickets (CASCADE delete), with person FK |
| `meetings` | Fireflies transcripts with summary, notes, action items |
| `meeting_attendees` | Attendees FK to meetings (CASCADE delete) and people |
| `epics` | Notion epics with properties, markdown content, status |
| `epic_sub_pages` | Sub-pages FK to epics (CASCADE delete) |

### Adding New Migrations

```bash
# Generate a new migration from model changes
alembic revision --autogenerate -m "add_new_column"

# Apply
alembic upgrade head
```

## Required Config

After the API starts and tables exist, the `configs` table is **empty**. Each data source needs runtime config before `/fetch` will do anything useful. Seed it via the API:

### Slack (required for `/slack/fetch`)

```bash
# Map of channel names to Slack channel IDs
curl -X PUT localhost:8100/api/v1/config/slack/watched_channels \
  -H 'Content-Type: application/json' \
  -d '{"value": {"engineering": "C0XXXXXXX", "general": "C0YYYYYYY"}}'

# People whose DMs to fetch (display names)
curl -X PUT localhost:8100/api/v1/config/slack/watched_dm_people \
  -H 'Content-Type: application/json' \
  -d '{"value": ["Alice Smith", "Bob Jones"]}'

# Your Slack user ID (for identifying your own messages)
curl -X PUT localhost:8100/api/v1/config/slack/user_id \
  -H 'Content-Type: application/json' \
  -d '{"value": "U0XXXXXXX"}'

# Workspace ID (for constructing DM channel IDs)
curl -X PUT localhost:8100/api/v1/config/slack/workspace \
  -H 'Content-Type: application/json' \
  -d '{"value": "T0XXXXXXX"}'
```

**How to find channel IDs**: Right-click a channel in Slack > "View channel details" > scroll to bottom. Or use `POST /api/v1/slack/channels` with a channel name to auto-resolve it.

### Linear (required for `/linear/fetch`)

```bash
# Your Linear team name (matches the team in Linear's sidebar)
curl -X PUT localhost:8100/api/v1/config/linear/team_name \
  -H 'Content-Type: application/json' \
  -d '{"value": "My Team"}'
```

The fetcher finds the active cycle for the requested week and pulls all tickets + comments.

### Meets / Fireflies (required for `/meets/fetch`)

```bash
# Your email (only meetings with this attendee are fetched)
curl -X PUT localhost:8100/api/v1/config/meets/participant_email \
  -H 'Content-Type: application/json' \
  -d '{"value": "you@example.com"}'

# People to exclude from 1:1 detection (optional)
curl -X PUT localhost:8100/api/v1/config/meets/excluded_names \
  -H 'Content-Type: application/json' \
  -d '{"value": ["Bot User"]}'
```

### Epics / Notion (required for `/epics/fetch`)

```bash
# Notion database URL (the database containing your epics)
curl -X PUT localhost:8100/api/v1/config/epics/notion_database_url \
  -H 'Content-Type: application/json' \
  -d '{"value": "https://notion.so/your-db-id"}'

# Statuses to fetch (only pages with these statuses are included)
curl -X PUT localhost:8100/api/v1/config/epics/active_statuses \
  -H 'Content-Type: application/json' \
  -d '{"value": ["In development", "Ready for dev", "Planning"]}'
```

### Verify Config

```bash
# See all config
curl localhost:8100/api/v1/config | python3 -m json.tool

# See config for one source
curl localhost:8100/api/v1/config/slack | python3 -m json.tool
```

## First Fetch

After config is seeded, fetch data for the current week:

```bash
curl -X POST localhost:8100/api/v1/slack/fetch     # Slack messages
curl -X POST localhost:8100/api/v1/linear/fetch    # Linear tickets
curl -X POST localhost:8100/api/v1/meets/fetch     # Fireflies meetings
curl -X POST localhost:8100/api/v1/epics/fetch     # Notion epics
```

Each returns a summary with record counts and any warnings. Check the health endpoint to confirm:

```bash
curl localhost:8100/api/v1/health | python3 -m json.tool
```

## API Reference

All endpoints accept an optional `?week=YYYY-MM-DD` param. Any date snaps to its Monday-Sunday week. No param = current week.

### Data Sources

#### Slack
```
POST /api/v1/slack/fetch?week=2026-03-30    Fetch from Slack API, store in DB
GET  /api/v1/slack?week=2026-03-30          Read stored messages
     &channel=engineering                    Filter by channel name
     &is_dm=true                            Filter DMs only
     &is_thread_reply=false                 Filter parent messages only
     &person_id=<uuid>                      Filter by person
     &limit=100&offset=0                    Pagination

POST /api/v1/slack/channels                 Add channel by name (auto-resolves ID)
     body: {"name": "my-channel"}
POST /api/v1/slack/channels/by-id           Add channel with known ID
     ?name=my-channel&channel_id=C09JHP7NPAL
```

#### Linear
```
POST /api/v1/linear/fetch?week=2026-03-30   Fetch cycle tickets from Linear API
GET  /api/v1/linear?week=2026-03-30         Read stored tickets (comments nested)
     &status_type=in_progress               Filter: todo|in_progress|done|discarded
     &identifier=TEAM-1234                   Filter by ticket ID
     &label=Backend                         Filter by label
     &priority=2                            Filter by priority (0-4)
     &person_id=<uuid>                      Filter by assignee

PATCH /api/v1/linear/tickets/{identifier}   Update ticket relationships/fields
      body: {"parent": "TEAM-456"}           Set parent (or null to detach)
      body: {"children": ["TEAM-A","TEAM-B"]} Set children (array order = sort order)
      body: {"title": "...", "labels": []}  Update fields (title, description, labels, status, assignee, priority, points)

POST  /api/v1/linear/tickets                Create new ticket
      body: {"title": "...", "parent": "TEAM-123", "labels": ["backend"], "priority": 2}
```

#### Meets (Fireflies)
```
POST /api/v1/meets/fetch?week=2026-03-30    Fetch from Fireflies API
GET  /api/v1/meets?week=2026-03-30          Read stored meetings (attendees nested)
     &title=planning                        Search by title
     &person_id=<uuid>                      Filter by attendee
```

#### Epics (Notion)
```
POST /api/v1/epics/fetch?week=2026-03-30    Fetch from Notion API (or push JSON body)
GET  /api/v1/epics?week=2026-03-30          Read stored epics (sub-pages nested)
     &status=In%20development               Filter by status
     &title=agent                           Search by title
```

Epics push body (legacy, when Notion can't be fetched from container):
```json
{
  "week": "2026-03-30",
  "epics": [{
    "notion_page_id": "page-1",
    "title": "Agent Always-On",
    "status": "In development",
    "team": ["Agents Core"],
    "pm_lead": "Sam",
    "sort_order": 1,
    "content": "# Epic content",
    "sub_pages": [{"notion_page_id": "sub-1", "title": "Sub Page", "content": "..."}]
  }]
}
```

#### Epics Police
```
GET  /api/v1/epics-police/analysis          Latest analysis JSON (pushed by epics-police skill)
POST /api/v1/epics-police/analysis          Store analysis JSON (body: full analysis object)
GET  /epics-police                          Interactive ticket hierarchy UI
```

### Formatted Output

Each source has a `/formatted` endpoint that returns rendered markdown files (one per channel/ticket/meeting/epic):

```
GET /api/v1/slack/formatted?week=...        Markdown per channel (messages grouped by day)
GET /api/v1/linear/formatted?week=...       Markdown per ticket (with comments, resources)
GET /api/v1/meets/formatted?week=...        Markdown per meeting (summary, action items)
GET /api/v1/epics/formatted?week=...        Markdown per epic (properties, content, sub-pages)
```

Templates are customizable via config: `PUT /api/v1/config/{source}/formatted_template`.

### Config
```
GET    /api/v1/config                       All configs grouped by source
GET    /api/v1/config/{source}              Configs for: slack|linear|meets|epics|general
GET    /api/v1/config/{source}/{key}        Single config value
PUT    /api/v1/config/{source}/{key}        Create/update config (body: {"value": ...})
DELETE /api/v1/config/{source}/{key}        Delete config
```

### People
```
GET   /api/v1/people                        List people (cross-linked identities)
      &name=john&squad=backend               Filter by name, squad, email
GET   /api/v1/people/{id}                   Single person
PATCH /api/v1/people/{id}                   Update person fields
```

### Utility
```
GET /api/v1/health       DB status + last fetch timestamp per source
GET /api/v1/weeks        All weeks with record counts per source
```

## Week Resolution

Send any date -- the API snaps to Monday-Sunday:
- `?week=2026-04-08` (Wednesday) -> Mon Apr 6 - Sun Apr 12
- `?week=2026-04-12` (Sunday) -> Mon Apr 6 - Sun Apr 12
- No param -> current week

Date range per source:
- Slack: Monday 00:00:00 UTC to Sunday 23:59:59 UTC
- Meets: Monday to next Monday (exclusive)
- Linear: by cycle (1 cycle = 1 week)
- Epics: push-based, caller controls

## Key Concepts

### Historical Data Protection

Fetching a past week that already has data returns `409 Conflict`. This is intentional -- historical snapshots are immutable. Only the current week can be re-fetched (data is upserted).

### Person Cross-Linking

The `persons` table maps identities across systems. Each fetcher creates placeholder Person records (e.g., from a Slack user ID), then enriches them post-fetch (adds email, display name). When enrichment reveals two placeholders share an email, they're merged automatically.

### Slack Deep Links

All Slack messages store `channel_id` and `slack_ts` for deep linking:
```
https://{workspace}.slack.com/archives/{channel_id}/p{ts_without_dot}
Thread: ...?thread_ts={parent_ts_without_dot}
```

The workspace URL is set via `SLACK_WORKSPACE_URL` env var or config: `GET /api/v1/config/slack/workspace_url`.

## Architecture

```
docker compose
  +-- api (FastAPI/Python 3.12, port 8100)
  +-- db  (PostgreSQL 16, port 5433)
```

The API fetches directly from Slack, Linear, Fireflies, and Notion APIs.

### Project Structure

```
app/
  main.py              FastAPI app + router registration
  database.py          SQLAlchemy async engine + session
  core/
    config.py          Pydantic BaseSettings (.env)
    week_utils.py      resolve_week(), week_label(), month_dir()
    http_client.py     Shared httpx client with retry/backoff
    mentions.py        Slack/Linear mention replacement
    templating.py      Jinja2 environment for formatted output
  models/              SQLAlchemy models (11 tables)
  schemas/             Pydantic request/response models
  routers/             FastAPI route handlers (8 routers)
  services/            Business logic (fetchers, people resolution, config CRUD)
tests/
  conftest.py          Test DB setup, fixtures (management_test on port 5433)
  test_*.py            Unit + e2e tests
bruno/                 Bruno API collection (Local + Docker envs)
alembic/
  versions/            Migration files (001_initial_tables.py)
  env.py               Async migration runner
scripts/
  setup-local.sh       Local dev bootstrap
```

## Development

### Running Tests

Tests use a separate `management_test` database on the same PostgreSQL instance (port 5433). The `setup-local.sh` script creates it, or manually:

```bash
docker compose exec db psql -U mgmt -d management -c "CREATE DATABASE management_test"
pytest tests/ -v
```

Each test creates and drops all tables, so there's no state leakage between tests.

### Bruno Collection

Open the `bruno/` folder in [Bruno](https://www.usebruno.com/). Select the "Local" environment. All endpoints are pre-configured with example parameters.

### Adding a New Migration

```bash
# After modifying models
alembic revision --autogenerate -m "describe_change"
alembic upgrade head
```

## Periodic Fetch (launchd)

```bash
jura api job start           # Every 20 min (default)
jura api job start 600       # Every 10 min
jura api job start 3600      # Every hour
jura api job status          # Check if active
jura api job stop            # Remove the job
```

Runs Slack, Linear, and Meets fetches sequentially via macOS launchd. Notion excluded (fetched separately). Single process -- no overlap or race conditions. Survives terminal closes and Mac restarts.

## Backup & Restore

```bash
./backup_db.sh                              # Backup to backups/management_YYYYMMDD_HHMMSS.sql.gz
gunzip -c backups/<file>.sql.gz | docker compose exec -T db psql -U mgmt management  # Restore
```

Data persists through container rebuilds and Docker restarts. Only `docker compose down -v` destroys the volume.

## Environment Variables

`.env` holds secrets and connection info. All behavioral config lives in the `configs` DB table (managed via API).

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | auto | PostgreSQL connection (Docker overrides this; `.env.example` has localhost:5433 for local dev) |
| `SLACK_BOT_TOKEN` | for Slack | Slack User OAuth token (xoxp-...) |
| `LINEAR_API_KEY` | for Linear | Linear personal API key |
| `FIREFLIES_API_KEY` | for Meets | Fireflies API key |
| `NOTION_API_KEY` | for Epics | Notion internal integration token |
| `SLACK_WORKSPACE_URL` | optional | Slack workspace URL (for deep links) |
| `API_PORT` | optional | API port (default 8100) |
| `LOG_LEVEL` | optional | Logging level (default info) |

## Troubleshooting

### Migrations fail on startup (Docker)

Check container logs: `docker compose logs api`. Common causes:
- DB not ready yet (health check should prevent this, but check `docker compose ps`)
- Stale migration state: `docker compose exec db psql -U mgmt -d management -c "SELECT * FROM alembic_version"`

### `alembic upgrade head` fails locally

- Is PostgreSQL running? `docker compose ps db`
- Is `DATABASE_URL` correct in `.env`? For local dev it should be `postgresql+asyncpg://mgmt:mgmt@localhost:5433/management`
- Is the venv activated? `source .venv/bin/activate`

### Fetch returns empty data

- Check config is seeded: `curl localhost:8100/api/v1/config/{source}`
- For Slack: `watched_channels` must map channel names to IDs
- For Linear: `team_name` must match exactly (case-sensitive)
- For Meets: `participant_email` must match a Fireflies user email
- Check API keys are valid in `.env`

### 409 on fetch

Historical week already has data. This is intentional. Only current week can be re-fetched.

## Migration Scripts

| Script | Purpose |
|--------|---------|
| `migrate_historical.py` | Re-fetch Linear/Meets/Epics from APIs for historical weeks |
| `migrate_slack_direct.py` | Fetch Slack from API directly (bypasses HTTP timeout) |
