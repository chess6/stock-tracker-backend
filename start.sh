#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.backend.pid"
LOG_FILE="$SCRIPT_DIR/backend.out"

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE")"
  if kill -0 "$EXISTING_PID" >/dev/null 2>&1; then
    echo "Backend is already running on PID $EXISTING_PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$SCRIPT_DIR"
# Disable the Flask debug reloader so the background PID stays valid.
FLASK_DEBUG=0 nohup python3 api.py >"$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" >"$PID_FILE"
sleep 1

if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
  echo "Backend started on PID $BACKEND_PID"
  echo "Log: $LOG_FILE"
else
  if grep -q "Address already in use" "$LOG_FILE" 2>/dev/null; then
    echo "Backend failed: port 5000 is already in use."
    echo "Run ./stop.sh first, or stop the process using that port."
  else
    echo "Backend failed to start. Check $LOG_FILE"
  fi
  rm -f "$PID_FILE"
  exit 1
fi
