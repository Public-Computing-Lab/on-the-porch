#!/usr/bin/env bash
# Phase 3 CI checks: golden facts (no API), regression + latency (API on :8888).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

API_BASE="${API_BASE:-http://127.0.0.1:8888}"
FAILED=0

echo "=== Golden fact tests (in-process) ==="
if ! "$PYTHON" scripts/golden_fact_test.py; then
  FAILED=1
fi

echo ""
echo "=== Transcript vectordb health ==="
if ! "$PYTHON" scripts/check_transcript_vectordb.py; then
  FAILED=1
fi

echo ""
echo "=== Chat regression (HTTP ${API_BASE}) ==="
if curl -sf "${API_BASE}/health" >/dev/null 2>&1; then
  if ! OTP_API_BASE_URL="$API_BASE" "$PYTHON" scripts/chat_regression_test.py; then
    FAILED=1
  fi
else
  echo "SKIP: API not reachable at ${API_BASE} — start api/api_v2.py for full regression"
fi

echo ""
echo "=== Path latency audit (in-process) ==="
if ! "$PYTHON" scripts/path_latency_audit.py --in-process-only; then
  FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
  echo ""
  echo "CI checks FAILED"
  exit 1
fi

echo ""
echo "CI checks PASSED"
