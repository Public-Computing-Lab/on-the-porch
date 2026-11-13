#!/usr/bin/env bash
# Wipe ingest knowledge and run a fresh daily ingestion (dotnews disabled in cron).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  PYTHON="$(command -v python)"
fi

echo "This will TRUNCATE civic/event/RAG MySQL tables and wipe the vector store."
echo "Users and conversations are NOT deleted."
echo

exec "$PYTHON" scripts/reset_knowledge.py --yes --reingest "$@"
