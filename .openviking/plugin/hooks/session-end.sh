#!/usr/bin/env bash
# SessionEnd hook: commit OpenViking session and extract long-term memories.
# NOTE: This hook runs async (up to 120s). To avoid race conditions with
# the next session's SessionStart, we snapshot the session_id BEFORE calling
# the bridge, then let the bridge verify it still owns the state file.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [[ ! -f "$OV_CONF" || ! -f "$STATE_FILE" ]]; then
  exit 0
fi

# Snapshot session_id before the (potentially slow) commit
CURRENT_SID="$(_json_val "$(cat "$STATE_FILE")" "session_id" "")"
if [[ -z "$CURRENT_SID" ]]; then
  exit 0
fi

OUT="$(run_bridge session-end --expected-session-id "$CURRENT_SID" 2>/dev/null || true)"
STATUS="$(_json_val "$OUT" "status_line" "")"

if [[ -n "$STATUS" ]]; then
  json_status=$(_json_encode_str "$STATUS")
  echo "{\"systemMessage\": $json_status}"
  exit 0
fi

exit 0
