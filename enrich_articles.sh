#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://localhost:5000}"
BATCH="${BATCH:-50}"
SLEEP_SECONDS="${SLEEP_SECONDS:-1}"
RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

# Set FAST=1 for VADER + rules only (skip FinBERT and embeddings).
# Retag uses rules-only by default; set RETAG_EMBEDDINGS=1 to enable semantic matching.
if [ "${FAST:-0}" = "1" ]; then
  ENABLE_FINBERT="false"
  ENABLE_EMBEDDINGS="false"
else
  ENABLE_FINBERT="${ENABLE_FINBERT:-true}"
  ENABLE_EMBEDDINGS="${ENABLE_EMBEDDINGS:-true}"
fi
RETAG_EMBEDDINGS="${RETAG_EMBEDDINGS:-0}"

# FORCE=1 requeues completed articles and runs full enrichment again.
#   Do NOT use FORCE to refresh market reactions for Research/narrative — it only
#   processes the newest pending batch and may skip ticker-linked articles. Instead:
#     ./backfill_market_reactions.sh AAPL,MSFT
#   after refresh-macro (SPY benchmark) has run — see refresh_data.sh.
# RETAG=1 skips enrichment and only re-tags completed articles.
# RETAG_ALL=1 re-tags every completed article (not just those missing enrichment tags).
# SKIP_RETAG=1 disables the automatic post-enrichment entity-linking pass.
FORCE="${FORCE:-0}"
RETAG="${RETAG:-0}"
RETAG_ALL="${RETAG_ALL:-0}"
SKIP_RETAG="${SKIP_RETAG:-0}"

if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

if [ "$RETAG" = "1" ]; then
  PHASE="retag"
else
  PHASE="enrich"
fi
RETAG_OFFSET=0
FORCE_REQUEUE_DONE=0

build_json_payload() {
  local payload
  local finbert="${ENABLE_FINBERT}"
  local embeddings="${ENABLE_EMBEDDINGS}"
  if [ "$PHASE" = "retag" ]; then
    finbert="false"
    if [ "$RETAG_EMBEDDINGS" = "1" ]; then
      embeddings="true"
    else
      embeddings="false"
    fi
  fi
  payload="{\"enable_finbert\": ${finbert}, \"enable_embeddings\": ${embeddings}"
  if [ "$PHASE" = "retag" ]; then
    payload+=", \"retag_only\": true"
    if [ "$RETAG_ALL" = "0" ]; then
      payload+=", \"retag_all\": false"
    else
      payload+=", \"retag_all\": true"
    fi
    payload+=", \"offset\": ${RETAG_OFFSET}"
  fi
  payload+="}"
  printf '%s' "$payload"
}

build_requeue_payload() {
  printf '{"requeue_completed": true, "requeue_only": true, "enable_finbert": false, "enable_embeddings": false}'
}

build_request_url() {
  if [ "$PHASE" = "retag" ]; then
    local url="${BASE_URL}/api/admin/retag-articles?limit=${BATCH}&offset=${RETAG_OFFSET}"
    if [ "$RETAG_ALL" = "0" ]; then
      url+="&retagAll=false"
    else
      url+="&retagAll=true"
    fi
    printf '%s' "$url"
    return
  fi
  local url="${BASE_URL}/api/admin/enrich-articles?limit=${BATCH}"
  printf '%s' "$url"
}

bulk_requeue_completed() {
  local requeue_limit="${REQUEUE_LIMIT:-500}"
  local total_requeued=0
  echo "Requeueing completed articles in chunks of ${requeue_limit}..."
  while true; do
    local http_code
    http_code="$(curl "${curl_args[@]}" \
      "${BASE_URL}/api/admin/enrich-articles?limit=${BATCH}&requeueLimit=${requeue_limit}" \
      -d "$(build_requeue_payload)")"
    if [ "$http_code" -ge 400 ]; then
      echo "Requeue failed (HTTP ${http_code}):" >&2
      cat "$RESPONSE_FILE" >&2
      exit 1
    fi
    local requeued
    requeued="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("requeued", 0) or 0))' "$RESPONSE_FILE")"
    requeued=${requeued:-0}
    total_requeued=$((total_requeued + requeued))
    echo "  requeued=${requeued} (total ${total_requeued})"
    if [ "$requeued" -le 0 ]; then
      break
    fi
    sleep "$SLEEP_SECONDS"
  done
  FORCE_REQUEUE_DONE=1
  echo "Force requeue complete (${total_requeued} articles returned to pending)."
}

check_api_features() {
  local http_code
  http_code="$(curl -sS -o "$RESPONSE_FILE" -w "%{http_code}" "${BASE_URL}/api/admin/enrich-articles/status")"
  if [ "$http_code" -ge 400 ]; then
    echo "Could not reach API status endpoint (HTTP ${http_code})." >&2
    exit 1
  fi
  python3 -c '
import json, os, sys
payload = json.load(open(sys.argv[1]))
features = payload.get("api_features") or {}
if not features.get("retag_endpoint"):
    print("missing")
    sys.exit(0)
if os.environ.get("CHECK_EMBEDDINGS", "0") == "1" and not payload.get("embeddings_available"):
    err = payload.get("embeddings_error") or "embeddings unavailable"
    print("embeddings:" + err)
    sys.exit(0)
print("ok")
' "$RESPONSE_FILE"
}

curl_args=(
  -sS
  -o "$RESPONSE_FILE"
  -w "%{http_code}"
  -X POST
  -H "Content-Type: application/json"
)

if [ -n "${ADMIN_API_KEY:-}" ]; then
  curl_args+=(-H "X-Api-Key: ${ADMIN_API_KEY}")
fi

parse_batch_response() {
  python3 -c '
import json
import sys

payload = json.load(open(sys.argv[1]))
processed = int(payload.get("processed", 0) or 0)
batch_ok = sum(1 for item in payload.get("results", []) if item.get("status") in {"complete", "retagged"})
errors = sum(1 for item in payload.get("results", []) if item.get("status") == "error")
pipeline = payload.get("pipeline") or {}
pending = int(pipeline.get("pending", 0) or 0)
processing = int(pipeline.get("processing", 0) or 0)
articles_complete = int(pipeline.get("complete", 0) or 0)
recovered = int(payload.get("recovered", 0) or 0)
requeued = int(payload.get("requeued", 0) or 0)
remaining_retag = int(payload.get("remaining_retag", 0) or 0)
next_offset = int(payload.get("next_offset", 0) or 0)
mode = payload.get("mode") or "enrich"
print(processed, batch_ok, errors, pending, processing, articles_complete, recovered, requeued, remaining_retag, next_offset, mode)
' "$RESPONSE_FILE"
}

echo "Enriching articles in batches of ${BATCH}"
echo "API: ${BASE_URL}"
echo "FinBERT: ${ENABLE_FINBERT} | Embeddings: ${ENABLE_EMBEDDINGS}"
echo "NLP_DEVICE: ${NLP_DEVICE:-auto} (GPU batch kicks in at 8+ articles when CUDA is available)"
if [ "$RETAG" = "1" ]; then
  echo "Mode: RETAG-ONLY (entity linking on completed articles)"
elif [ "$FORCE" = "1" ]; then
  echo "Mode: FORCE (requeue completed articles, full enrichment)"
  echo "Note: for Research narrative market reactions use ./backfill_market_reactions.sh instead"
else
  echo "Mode: ENRICH + auto entity-linking pass for completed articles"
fi
echo

if [ "$ENABLE_EMBEDDINGS" = "true" ]; then
  CHECK_EMBEDDINGS=1 feature_status="$(check_api_features)"
else
  feature_status="$(check_api_features)"
fi
if [ "$feature_status" = "missing" ]; then
  echo "Backend is running old code without the retag endpoint." >&2
  echo "Restart it, then rerun this script:" >&2
  echo "  ./restart.sh" >&2
  exit 1
fi
if [[ "$feature_status" == embeddings:* ]]; then
  echo "${feature_status#embeddings:}" >&2
  echo "Install NLP dependencies, restart the backend, then rerun:" >&2
  echo "  pip install -r requirements-nlp.txt" >&2
  echo "  ./restart.sh" >&2
  exit 1
fi

if [ "$FORCE" = "1" ] && [ "$PHASE" = "enrich" ]; then
  bulk_requeue_completed
fi

round=0
while true; do
  round=$((round + 1))
  echo "Round ${round} (phase=${PHASE})"
  echo "  Calling API (each batch runs synchronously; large batches can take minutes)..."

  request_args=("${curl_args[@]}" "$(build_request_url)" -d "$(build_json_payload)")
  http_code="$(curl "${request_args[@]}")"
  if [ "$http_code" -ge 400 ]; then
    echo "Enrichment failed (HTTP ${http_code}):" >&2
    cat "$RESPONSE_FILE" >&2
    echo >&2
    if [ "$http_code" = "503" ] && grep -q '"retry"[[:space:]]*:[[:space:]]*true' "$RESPONSE_FILE" 2>/dev/null; then
      echo "Database lock — stop ./worker.sh or wait, then rerun." >&2
    fi
    exit 1
  fi

  read -r processed batch_ok errors pending processing articles_complete recovered requeued remaining_retag next_offset mode < <(parse_batch_response)
  processed=${processed:-0}
  batch_ok=${batch_ok:-0}
  errors=${errors:-0}
  pending=${pending:-0}
  processing=${processing:-0}
  articles_complete=${articles_complete:-0}
  recovered=${recovered:-0}
  requeued=${requeued:-0}
  remaining_retag=${remaining_retag:-0}
  next_offset=${next_offset:-0}

  elapsed="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("elapsed_seconds",""))' "$RESPONSE_FILE" 2>/dev/null || true)"
  echo "  processed=${processed} batch_ok=${batch_ok} errors=${errors} pending=${pending} processing=${processing} articles_complete=${articles_complete} recovered=${recovered} requeued=${requeued} remaining_retag=${remaining_retag} mode=${mode} elapsed=${elapsed}s"

  if [ "$processed" -gt 0 ]; then
    if [ "$PHASE" = "retag" ]; then
      RETAG_OFFSET="$next_offset"
    fi
    sleep "$SLEEP_SECONDS"
    continue
  fi

  if [ "$PHASE" = "retag" ]; then
    if [ "$mode" != "retag" ]; then
      echo "  Expected retag mode but API returned mode=${mode}." >&2
      echo "  Restart the backend (./stop.sh && ./start.sh) to load the latest code." >&2
      exit 1
    fi
    if [ "$processed" -eq 0 ] && [ "$remaining_retag" -gt 0 ]; then
      echo "  Entity-linking pass continues (${remaining_retag} remaining)..." >&2
      RETAG_OFFSET="$next_offset"
      sleep "$SLEEP_SECONDS"
      continue
    fi
    if [ "$remaining_retag" -gt 0 ]; then
      RETAG_OFFSET="$next_offset"
      sleep "$SLEEP_SECONDS"
      continue
    fi
    echo "All articles enriched and entity-linked."
    break
  fi

  if [ "$pending" -gt 0 ] || [ "$processing" -gt 0 ]; then
    echo "  No articles processed this round but enrichment work remains; retrying..." >&2
    sleep "$SLEEP_SECONDS"
    continue
  fi

  if [ "$SKIP_RETAG" = "1" ]; then
    echo "Enrichment complete. Entity-linking pass skipped (SKIP_RETAG=1)."
    break
  fi

  if [ "$articles_complete" -gt 0 ]; then
    echo "Enrichment complete. Starting automatic entity-linking pass on completed articles..."
    PHASE="retag"
    RETAG_OFFSET=0
    sleep "$SLEEP_SECONDS"
    continue
  fi

  echo "All articles enriched."
  break
done
