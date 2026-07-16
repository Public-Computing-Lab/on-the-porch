"""Gunicorn configuration for the RethinkAI API (production server).

Run with:
    gunicorn -c api/gunicorn.conf.py api_v2:app
    gunicorn -c gunicorn.conf.py api_v2:app   # DreamHost / legacy cwd
or simply:
    ./start_api.sh

Every setting can be overridden with an environment variable so the same config
works across machines without edits.
"""

import os
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_API_DIR = _THIS_FILE.parent
_ROOT_DIR = _API_DIR.parent


def _load_env_files() -> None:
    """Load .env before reading GUNICORN_* (gunicorn does not source .env itself)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_ROOT_DIR / ".env", override=False)
    load_dotenv(_API_DIR / ".env", override=False)


_load_env_files()

# Run from the repo root, but make `import api_v2` (and its sibling modules)
# importable by putting the api/ directory on the path.
chdir = str(_ROOT_DIR)
pythonpath = str(_API_DIR)

# --- Networking -------------------------------------------------------------
bind = os.getenv(
    "GUNICORN_BIND",
    f"{os.getenv('API_HOST', '127.0.0.1')}:{os.getenv('API_PORT', '8888')}",
)

# --- Concurrency ------------------------------------------------------------
# gthread: each worker is a process running a pool of threads. Keep the default
# conservative for an 8 GB DreamHost host because Chroma/local embeddings and
# Gemini clients duplicate memory per worker.
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# --- Timeouts ---------------------------------------------------------------
# Backend chat budget defaults to about 30s and the frontend waits about 55s.
# Gunicorn should be above both but below the old 180s runaway-request window.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "75"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# --- Memory hygiene ---------------------------------------------------------
# Periodically recycle workers to bound any slow leaks (LLM/vector clients).
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "500"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# --- Worker initialization --------------------------------------------------
# Do NOT preload the app. Each worker must build its own MySQL connection pool
# and Chroma client AFTER forking; sharing those across forked processes
# corrupts pooled sockets and SQLite handles.
preload_app = False

# --- Logging ----------------------------------------------------------------
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")


def when_ready(server) -> None:  # noqa: ARG001
    """Log effective worker settings (visible in gunicorn error log on boot)."""
    print(
        f"gunicorn config: workers={workers} worker_class={worker_class} "
        f"bind={bind} timeout={timeout}",
        flush=True,
    )
    # Defense in depth: re-check production contract after master is ready.
    # Workers also validate on api_v2 import when PRODUCTION_MODE=true.
    if (os.getenv("PRODUCTION_MODE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        from launch_runtime import assert_launch_runtime

        assert_launch_runtime(production_mode=True)
