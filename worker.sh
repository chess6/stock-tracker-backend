#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.worker.pid"
LOG_FILE="$SCRIPT_DIR/worker.out"

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE")"
  if kill -0 "$EXISTING_PID" >/dev/null 2>&1; then
    echo "Worker is already running on PID $EXISTING_PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$SCRIPT_DIR"
nohup python3 worker.py >"$LOG_FILE" 2>&1 &
WORKER_PID=$!
echo "$WORKER_PID" >"$PID_FILE"
sleep 1

if kill -0 "$WORKER_PID" >/dev/null 2>&1; then
  echo "Worker started on PID $WORKER_PID"
  echo "Log: $LOG_FILE"
else
  echo "Worker failed to start. Check $LOG_FILE"
  rm -f "$PID_FILE"
  exit 1
fi
