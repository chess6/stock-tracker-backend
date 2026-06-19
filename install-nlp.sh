#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing pinned NLP dependencies (sentence-transformers + FinBERT stack)..."
python3 -m pip install -r requirements-nlp.txt

python3 -c "
from app.services.embeddings_service import embedding_model_status
ok, err = embedding_model_status()
if not ok:
    raise SystemExit(err or 'embedding model check failed')
print('Embedding model OK')
"

echo "Done. Restart the backend before running enrichment with embeddings."
