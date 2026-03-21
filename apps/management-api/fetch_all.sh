#!/bin/bash
# Fetch all sources (except Notion) from the Management API.
# Called by launchd job or manually.

API="http://localhost:8100/api/v1"
LOG_DIR="$(dirname "$0")/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fetch.log"

ts() { date +"%Y-%m-%d %H:%M:%S"; }

# Check API is healthy
if ! curl -sf "$API/health" > /dev/null 2>&1; then
  echo "$(ts) SKIP — API not running" >> "$LOG"
  exit 0
fi

echo "$(ts) START fetch" >> "$LOG"

# Fetch Slack, Linear, Meets in sequence (not parallel — avoid rate limits)
for source in slack linear meets; do
  result=$(curl -s -X POST "$API/$source/fetch" 2>&1)
  status=$?
  if echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('week_label',''))" 2>/dev/null; then
    count=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('messages', d.get('tickets', d.get('meetings', '?'))))" 2>/dev/null)
    echo "$(ts) OK    $source — $count records" >> "$LOG"
  else
    # Could be 409 (in progress / historical protected) or 500
    code=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('detail',{}).get('code','error'))" 2>/dev/null || echo "error")
    echo "$(ts) SKIP  $source — $code" >> "$LOG"
  fi
done

echo "$(ts) DONE" >> "$LOG"
