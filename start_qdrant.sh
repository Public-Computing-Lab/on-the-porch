#!/usr/bin/env bash
# Optional Qdrant server for VECTOR_DB_PROVIDER=qdrant with QDRANT_MODE=server.
# For local dev, prefer QDRANT_MODE=local (embedded, no Docker).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${QDRANT_DATA_DIR:-$ROOT_DIR/on_the_porch/qdrant_data_server}"
PORT="${QDRANT_PORT:-6333}"

mkdir -p "$DATA_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found. Use QDRANT_MODE=local in .env instead (no server required)." >&2
  exit 1
fi

echo "Starting Qdrant on http://127.0.0.1:${PORT} (data: $DATA_DIR)"
exec docker run --rm -it \
  -p "${PORT}:6333" \
  -v "${DATA_DIR}:/qdrant/storage" \
  qdrant/qdrant:latest
