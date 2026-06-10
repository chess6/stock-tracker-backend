#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

# Smoke-test pipeline modes against a running backend.
# Usage:
#   BASE_URL=http://localhost:5000 ./scripts/test_pipeline_modes.sh
#   TICKERS=AAPL,MSFT ./scripts/test_pipeline_modes.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_URL="${BASE_URL:-http://localhost:5000}"
TICKERS="${TICKERS:-AAPL,MSFT}"

post_mode() {
  local mode="$1"
  local extra_query="${2:-}"
  echo "== mode=${mode} ${extra_query}"
  curl -sS -X POST "${BASE_URL}/api/admin/pipeline-refresh?mode=${mode}${extra_query}" \
    -H "Content-Type: application/json" \
    -d "{\"tickers\":[\"${TICKERS//,/\",\"}\"]}"
  echo
}

post_mode "recompute_scores_only"
post_mode "refresh_stale_only" "&tickers=${TICKERS}"
post_mode "refresh_missing_only" "&tickers=${TICKERS}"
post_mode "refresh_prices_only"
post_mode "lightweight_daily_refresh"
post_mode "force_refresh" "&tickers=${TICKERS}"

echo "Pipeline mode smoke complete."
