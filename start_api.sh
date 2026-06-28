#!/usr/bin/env bash
# Start the RethinkAI API under gunicorn (production server).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Prefer a project virtualenv if present, else fall back to PATH.
if [ -x ".venv/bin/gunicorn" ]; then
  GUNICORN=".venv/bin/gunicorn"
elif [ -x ".venv/Scripts/gunicorn.exe" ]; then
  GUNICORN=".venv/Scripts/gunicorn.exe"
elif command -v gunicorn >/dev/null 2>&1; then
  GUNICORN="$(command -v gunicorn)"
else
  echo "ERROR: gunicorn not found. Install it (pip install gunicorn) or activate your venv." >&2
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "WARNING: no .env at $ROOT_DIR/.env; relying on the process environment." >&2
else
  # shellcheck disable=SC1091
  set -a
  source ".env"
  set +a
fi

if [ "${PRODUCTION_MODE:-false}" = "true" ] || [ "${PRODUCTION_MODE:-false}" = "1" ]; then
  if [ "${CACHE_BACKEND:-memory}" != "redis" ]; then
    echo "ERROR: PRODUCTION_MODE=true requires CACHE_BACKEND=redis in .env" >&2
    exit 1
  fi
  REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
  if command -v redis-cli >/dev/null 2>&1; then
    if ! redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1; then
      echo "ERROR: Redis is not reachable at $REDIS_URL. Start it with ./start_redis.sh" >&2
      exit 1
    fi
  else
    echo "WARNING: redis-cli not found; skipping Redis reachability check." >&2
  fi
  echo "Production mode: CACHE_BACKEND=redis, starting gunicorn..."
fi

exec "$GUNICORN" -c "$ROOT_DIR/api/gunicorn_conf.py" api_v2:app
