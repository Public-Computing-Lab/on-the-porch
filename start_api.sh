#!/usr/bin/env bash
# Start the RethinkAI API under gunicorn (production server).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".env" ]; then
  echo "WARNING: no .env at $ROOT_DIR/.env; relying on the process environment." >&2
else
  # shellcheck disable=SC1091
  set -a
  source ".env"
  set +a
fi

if [ "${CACHE_BACKEND:-}" = "redis" ] || [ "${SEMANTIC_CACHE_BACKEND:-}" = "redis" ]; then
  echo "ERROR: Redis is not supported. Set CACHE_BACKEND=mysql and SEMANTIC_CACHE_BACKEND=mysql." >&2
  exit 1
fi

# Prefer a project virtualenv if present, else fall back to PATH.
if [ -x ".venv/bin/gunicorn" ]; then
  GUNICORN=".venv/bin/gunicorn"
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/gunicorn.exe" ]; then
  GUNICORN=".venv/Scripts/gunicorn.exe"
  PYTHON=".venv/Scripts/python.exe"
elif [ -x "../ml-misi-community-sentiment/.venv/bin/gunicorn" ]; then
  GUNICORN="../ml-misi-community-sentiment/.venv/bin/gunicorn"
  PYTHON="../ml-misi-community-sentiment/.venv/bin/python"
elif command -v gunicorn >/dev/null 2>&1; then
  GUNICORN="$(command -v gunicorn)"
  PYTHON="$(command -v python3 || command -v python)"
else
  echo "ERROR: gunicorn not found. Install it (pip install gunicorn) or activate your venv." >&2
  exit 1
fi

if [ "${PRODUCTION_MODE:-false}" = "true" ] || [ "${PRODUCTION_MODE:-false}" = "1" ]; then
  CACHE_BACKEND="${CACHE_BACKEND:-mysql}"
  if [ "$CACHE_BACKEND" != "mysql" ]; then
    echo "ERROR: PRODUCTION_MODE=true requires CACHE_BACKEND=mysql in .env" >&2
    exit 1
  fi
  echo "Production mode: validating launch-runtime contract before bind..."
  PYTHONPATH="${ROOT_DIR}/api:${PYTHONPATH:-}" "$PYTHON" - <<'PY'
from launch_runtime import assert_launch_runtime
assert_launch_runtime(production_mode=True)
print("Launch runtime validation: ok", flush=True)
PY
fi

exec "$GUNICORN" -c "$ROOT_DIR/api/gunicorn.conf.py" api_v2:app
