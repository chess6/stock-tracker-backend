#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

timeout 300 python -m pytest \
  app/tests/test_scoring.py \
  app/tests/test_metrics_engine.py \
  app/tests/test_db_pipeline_schema.py \
  app/tests/test_composite_ranking.py \
  app/tests/test_rank_validation.py \
  app/tests/test_worker.py \
  -q --maxfail=3 -x

if [ "${CHECK_SCORING:-0}" = "1" ]; then
  timeout 300 python -m pytest \
    app/tests/test_scoring.py \
    app/tests/test_pillar_engine.py \
    app/tests/test_gate_engine.py \
    -q --maxfail=1 -x
fi
