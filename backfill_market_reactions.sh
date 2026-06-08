#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

# Recompute article_market_reactions from SQLite prices + SPY benchmark.
#
# Use this for Research narrative / topEvents — NOT enrich_articles.sh FORCE mode.
# Enrichment re-runs sentiment, entity linking, and events; it only updates market
# reactions for the newest pending batch and may skip ticker-linked articles.
#
# Prerequisites:
#   1. SPY benchmark prices loaded: curl -X POST .../api/admin/refresh-macro
#      (also run by refresh_data.sh)
#   2. Ticker prices loaded for symbols you care about
#
# Examples:
#   ./backfill_market_reactions.sh AAPL
#   ./backfill_market_reactions.sh AAPL,MSFT,GME
#   TICKER= AAPL ./backfill_market_reactions.sh          # all recent linked articles
#   LIMIT=200 ./backfill_market_reactions.sh AAPL
#
# Verify:
#   curl -s "http://localhost:5000/api/research/narrative/AAPL" | jq '.topEvents | length'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://localhost:5000}"
LIMIT="${LIMIT:-200}"
TICKERS="${*:-${TICKER:-}}"

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

curl_args=(
  -sS
  -o "$RESPONSE_FILE"
  -w "%{http_code}"
  -X POST
)

if [ -n "${ADMIN_API_KEY:-}" ]; then
  curl_args+=(-H "X-Api-Key: ${ADMIN_API_KEY}")
fi

if [ -z "$TICKERS" ]; then
  url="${BASE_URL}/api/admin/backfill-market-reactions?limit=${LIMIT}"
  echo "Backfilling market reactions (all recent linked articles, limit=${LIMIT})"
else
  IFS=',' read -r -a ticker_list <<< "$TICKERS"
  for symbol in "${ticker_list[@]}"; do
    symbol="$(echo "$symbol" | tr '[:lower:]' '[:upper:]' | xargs)"
    [ -n "$symbol" ] || continue
    url="${BASE_URL}/api/admin/backfill-market-reactions?ticker=${symbol}&limit=${LIMIT}"
    echo "Backfilling market reactions for ${symbol} (limit=${LIMIT})"
    http_code="$(curl "${curl_args[@]}" "$url")"
    if [ "$http_code" -ge 400 ]; then
      echo "Backfill failed for ${symbol} (HTTP ${http_code}):" >&2
      cat "$RESPONSE_FILE" >&2
      exit 1
    fi
    cat "$RESPONSE_FILE"
    echo
  done
  echo "Backfill complete"
  exit 0
fi

http_code="$(curl "${curl_args[@]}" "$url")"
if [ "$http_code" -ge 400 ]; then
  echo "Backfill failed (HTTP ${http_code}):" >&2
  cat "$RESPONSE_FILE" >&2
  exit 1
fi
cat "$RESPONSE_FILE"
echo
echo "Backfill complete"
