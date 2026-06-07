#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.backend.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Backend is not running"
  exit 0
fi

BACKEND_PID="$(cat "$PID_FILE")"
if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
  kill "$BACKEND_PID"
  for _ in {1..10}; do
    if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill -9 "$BACKEND_PID"
  fi
  echo "Backend stopped"
else
  echo "Backend PID file was stale"
fi

rm -f "$PID_FILE"

if command -v fuser >/dev/null 2>&1; then
  fuser -k 5000/tcp >/dev/null 2>&1 || true
elif command -v lsof >/dev/null 2>&1; then
  PORT_PID="$(lsof -ti tcp:5000 -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  if [ -n "$PORT_PID" ]; then
    kill "$PORT_PID" 2>/dev/null || kill -9 "$PORT_PID" 2>/dev/null || true
  fi
fi
