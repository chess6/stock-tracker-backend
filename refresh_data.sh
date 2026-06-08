#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

# Refresh fundamentals, prices, insiders, RSS, and macro benchmark (SPY, QQQ, …).
# Macro prices are required for article abnormal_return_1d / Research narrative topEvents.
# After ingest, run ./backfill_market_reactions.sh for tickers you track (do not use
# FORCE=1 enrich_articles.sh for that — see backfill_market_reactions.sh).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://localhost:5000}"
TICKERS="${*:-AAPL,MSFT,NVDA,AMD,GOOGL,AMZN,META,TSLA}"

curl -sS -X POST "$BASE_URL/api/admin/refresh-fundamentals?tickers=$TICKERS"
echo
curl -sS -X POST "$BASE_URL/api/admin/refresh-prices?tickers=$TICKERS"
echo
curl -sS -X POST "$BASE_URL/api/admin/refresh-insiders?tickers=$TICKERS"
echo
curl -sS -X POST "$BASE_URL/api/admin/ingest-default-feeds"
echo
curl -sS -X POST "$BASE_URL/api/admin/refresh-macro"
echo
echo "Refresh complete (run ./backfill_market_reactions.sh $TICKERS for Research narrative reactions)"
