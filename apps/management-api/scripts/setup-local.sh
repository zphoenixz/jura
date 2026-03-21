#!/bin/bash
# Bootstrap local development environment for the Management API.
# Run from apps/management-api/:  bash scripts/setup-local.sh
set -euo pipefail

API_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$API_DIR"

# ── Colors ──────────────────────────────────────────────────────────
green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
dim()   { printf '\033[2m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }

# ── 1. Check Docker is running ──────────────────────────────────────
if ! docker info > /dev/null 2>&1; then
  red "Docker is not running. Start Docker Desktop and re-run."
  exit 1
fi

# ── 2. .env file ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  green "Created .env from .env.example — edit it with your API keys."
  dim "  vi .env"
  echo ""
fi

# ── 3. Python venv + deps ──────────────────────────────────────────
if [ ! -d .venv ]; then
  green "Creating Python venv..."
  python3 -m venv .venv
fi
dim "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt

# ── 4. Start PostgreSQL ─────────────────────────────────────────────
green "Starting PostgreSQL (port 5433)..."
docker compose up -d db
dim "Waiting for PostgreSQL to be ready..."
until docker compose exec db pg_isready -U mgmt -d management -q 2>/dev/null; do
  sleep 1
done

# ── 5. Run migrations ──────────────────────────────────────────────
green "Running Alembic migrations..."
.venv/bin/alembic upgrade head
dim "Tables created: configs, persons, weeks, fetch_logs, slack_messages,"
dim "  linear_tickets, linear_comments, meetings, meeting_attendees, epics, epic_sub_pages"

# ── 6. Create test database ────────────────────────────────────────
if docker compose exec db psql -U mgmt -d management -tAc \
  "SELECT 1 FROM pg_database WHERE datname='management_test'" 2>/dev/null | grep -q 1; then
  dim "Test database management_test already exists."
else
  green "Creating test database (management_test)..."
  docker compose exec db psql -U mgmt -d management -c "CREATE DATABASE management_test"
fi

# ── Done ────────────────────────────────────────────────────────────
echo ""
green "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys (SLACK_BOT_TOKEN, LINEAR_API_KEY, etc.)"
echo "  2. Start the API:"
echo "       source .venv/bin/activate"
echo "       uvicorn app.main:app --reload --port 8100"
echo "  3. Open http://localhost:8100/docs"
echo "  4. Seed your config (see README.md 'Required Config' section)"
echo "  5. Run tests:"
echo "       pytest tests/ -v"
