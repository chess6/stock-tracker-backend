#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"
TICKERS="${*:-AAPL,MSFT,NVDA,AMD,GOOGL,AMZN,META,TSLA}"

curl -sS -X POST "$BASE_URL/api/admin/bootstrap?tickers=$TICKERS"
echo
echo "Bootstrap complete"
