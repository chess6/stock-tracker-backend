#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.backend.pid"
LOG_FILE="$SCRIPT_DIR/backend.out"
PORT="${PORT:-${BACKEND_PORT:-5000}}"

listener_pid() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$PORT" 2>/dev/null | awk -F'pid=' 'NR > 1 && /pid=/ { split($2, a, ","); print a[1]; exit }'
  fi
}

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE")"
  if kill -0 "$EXISTING_PID" >/dev/null 2>&1; then
    echo "Backend is already running on PID $EXISTING_PID"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

RUNNING_PID="$(listener_pid || true)"
if [[ -n "${RUNNING_PID:-}" ]]; then
  echo "$RUNNING_PID" >"$PID_FILE"
  echo "Backend is already running on port $PORT (PID $RUNNING_PID)"
  exit 0
fi

cd "$SCRIPT_DIR"
nohup gunicorn --config gunicorn.conf.py wsgi:app >"$LOG_FILE" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" >"$PID_FILE"
sleep 2

if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
  echo "Backend started on PID $BACKEND_PID"
  echo "Log: $LOG_FILE"
  exit 0
fi

RUNNING_PID="$(listener_pid || true)"
if [[ -n "${RUNNING_PID:-}" ]]; then
  echo "$RUNNING_PID" >"$PID_FILE"
  echo "Backend is running on port $PORT (PID $RUNNING_PID)"
  exit 0
fi

if grep -q "Address already in use" "$LOG_FILE" 2>/dev/null; then
  echo "Backend failed: port $PORT is already in use."
  echo "Run ./stop.sh first, or stop the process using that port."
else
  echo "Backend failed to start. Check $LOG_FILE"
  tail -20 "$LOG_FILE" 2>/dev/null || true
fi
rm -f "$PID_FILE"
exit 1
