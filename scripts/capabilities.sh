#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

# Print or manage Stock Tracker configuration, feature flags, and capabilities.
#
# Usage:
#   ./scripts/capabilities.sh                    # full reference + current flag state
#   ./scripts/capabilities.sh flags              # resolved feature flags only
#   ./scripts/capabilities.sh enable FLAG        # persist flag ON in SQLite app_config
#   ./scripts/capabilities.sh disable FLAG       # persist flag OFF in SQLite app_config
#   ./scripts/capabilities.sh env [FLAG]           # print export STOCK_TRACKER_FF_* lines
#
# Examples:
#   ./scripts/capabilities.sh enable experimental_research_queue
#   export STOCK_TRACKER_FF_EXPERIMENTAL_RESEARCH_QUEUE=true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$BACKEND_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$BACKEND_ROOT/.env"
  set +a
fi

exec python3 "$SCRIPT_DIR/show_capabilities.py" "$@"
