#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:5000}"
TICKERS="${*:-AAPL,MSFT,NVDA,AMD,GOOGL,AMZN,META,TSLA}"

RESPONSE_FILE="$(mktemp)"
HTTP_CODE="$(curl -sS -o "$RESPONSE_FILE" -w "%{http_code}" -X POST "$BASE_URL/api/admin/bootstrap?tickers=$TICKERS")"
cat "$RESPONSE_FILE"
echo
rm -f "$RESPONSE_FILE"

if [ "$HTTP_CODE" -ge 400 ]; then
  echo "Bootstrap failed (HTTP $HTTP_CODE)"
  exit 1
fi

echo "Bootstrap complete"
