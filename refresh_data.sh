#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://localhost:5000}"
TICKERS="${*:-AAPL,MSFT,NVDA,AMD,GOOGL,AMZN,META,TSLA}"

curl -sS -X POST "$BASE_URL/api/admin/refresh-fundamentals?tickers=$TICKERS"
echo
curl -sS -X POST "$BASE_URL/api/admin/ingest-default-feeds"
echo
echo "Refresh complete"
