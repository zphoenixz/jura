#!/usr/bin/env bash
# Browse OpenViking memories interactively
# Usage: bash .openviking-plugin/browse.sh [port]
PORT="${1:-1934}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$HOME/.local/pipx/venvs/openviking/bin/python3" "$SCRIPT_DIR/browse.py" "$PORT"
