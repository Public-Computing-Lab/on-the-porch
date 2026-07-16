"""
Central configuration for ml-misi-community-sentiment.

Loads .env from the project root. All apps (API, chatbot, ingestion) should
import settings from this module.

CLI:
  python config.py              # print summary
  python config.py -t           # test external connections
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ============================================================================
# Paths
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent
ON_THE_PORCH_DIR = PROJECT_ROOT / "on_the_porch"
DATA_INGESTION_DIR = ON_THE_PORCH_DIR / "data_ingestion"
RAG_DIR = ON_THE_PORCH_DIR / "rag stuff"
API_DIR = PROJECT_ROOT / "api"

_ENV_FILE = PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)

logger = logging.getLogger("config")


def log(msg: str) -> None:
    print(msg)


def log_info(msg: str) -> None:
    print(msg)


def log_success(msg: str) -> None:
    print(msg)


def log_warning(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


# ============================================================================
# Env helpers
# ============================================================================
def _env(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value:
        return value
    return default


def _truthy(key: str, default: str = "false") -> bool:
    return _env(key, default).strip().lower() in ("1", "true", "yes")


def _int(key: str, default: int) -> int:
    raw = _env(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    raw = _env(key, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _resolve_path(raw: str, base: Path) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _resolve_vectordb(raw: str, default_name: str) -> Path:
    value = raw or default_name
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (ON_THE_PORCH_DIR / path.name).resolve()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _split_csv(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


# ============================================================================
# API / Flask
# ============================================================================
API_VERSION = "v3.0"
RETHINKAI_API_KEYS = _split_csv(_env("RETHINKAI_API_KEYS", ""))

HOST = _env("API_HOST", "127.0.0.1")
PORT = _int("API_PORT", 8888)
DEBUG = _truthy("FLASK_DEBUG", "false")

SECRET_KEY = _env("FLASK_SECRET_KEY", "agent-api-secret-2025")
TOKEN_PEPPER = _env("TOKEN_PEPPER", "")
SESSION_COOKIE_SECURE = _truthy("FLASK_SESSION_COOKIE_SECURE", "false")

APP_BASE_URL = _env("APP_BASE_URL", "http://127.0.0.1:8000")
API_BASE_URL = _env("API_BASE_URL", f"http://{HOST}:{PORT}")
ALLOWED_ORIGINS = _split_csv(
    _env("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"),
)

AUTH_SESSION_COOKIE_NAME = _env("AUTH_SESSION_COOKIE_NAME", "otp_session")
AUTH_CSRF_COOKIE_NAME = _env("AUTH_CSRF_COOKIE_NAME", "otp_csrf")
SESSION_MAX_AGE_DAYS = _int("AUTH_SESSION_MAX_AGE_DAYS", 7)
SESSION_SAMESITE = _env("AUTH_SESSION_SAMESITE", "Lax")
GUEST_SESSION_HOURS = max(1, _int("GUEST_SESSION_HOURS", 24))
SESSION_TOUCH_DEBOUNCE_SECONDS = max(0, _int("SESSION_TOUCH_DEBOUNCE_SECONDS", 300))

LOGIN_WINDOW_MINUTES = _int("AUTH_LOGIN_WINDOW_MINUTES", 15)
LOGIN_LOCK_THRESHOLD = _int("AUTH_LOGIN_LOCK_THRESHOLD", 5)
LOGIN_LOCK_MINUTES = _int("AUTH_LOGIN_LOCK_MINUTES", 15)
AUTH_ADMIN_EMAILS = {
    _normalize_email(email)
    for email in _split_csv(_env("AUTH_ADMIN_EMAILS", ""))
    if _normalize_email(email)
}

GOOGLE_OAUTH_CLIENT_ID = _env("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = _env("GOOGLE_OAUTH_CLIENT_SECRET", "")
GOOGLE_OAUTH_DISCOVERY_URL = _env(
    "GOOGLE_OAUTH_DISCOVERY_URL",
    "https://accounts.google.com/.well-known/openid-configuration",
)
GOOGLE_OAUTH_REDIRECT_URI = _env(
    "GOOGLE_OAUTH_REDIRECT_URI",
    f"http://{HOST}:{PORT}/auth/google/callback",
)

RATE_LIMIT_ENABLED = _truthy("RATE_LIMIT_ENABLED", "true")
RATE_LIMIT_KEY_PREFIX = _env("RATE_LIMIT_KEY_PREFIX", "rethinkai:ratelimit:")
RATE_LIMIT_GUEST_CREATE_PER_IP = max(1, _int("RATE_LIMIT_GUEST_CREATE_PER_IP", 30))
RATE_LIMIT_GUEST_CREATE_WINDOW_SECONDS = max(
    60, _int("RATE_LIMIT_GUEST_CREATE_WINDOW_SECONDS", 3600),
)
RATE_LIMIT_CHAT_PER_SESSION = max(1, _int("RATE_LIMIT_CHAT_PER_SESSION", 60))
RATE_LIMIT_CHAT_WINDOW_SECONDS = max(60, _int("RATE_LIMIT_CHAT_WINDOW_SECONDS", 3600))
RATE_LIMIT_AUTH_PER_IP = max(1, _int("RATE_LIMIT_AUTH_PER_IP", 30))
RATE_LIMIT_AUTH_WINDOW_SECONDS = max(60, _int("RATE_LIMIT_AUTH_WINDOW_SECONDS", 900))

MAX_MESSAGE_LENGTH = max(500, _int("MAX_MESSAGE_LENGTH", 8000))
MAX_COMMUNITY_NOTE_LENGTH = max(500, _int("MAX_COMMUNITY_NOTE_LENGTH", 4000))

GUEST_CLEANUP_ENABLED = _truthy("GUEST_CLEANUP_ENABLED", "false")
GUEST_CLEANUP_INACTIVE_HOURS = _int("GUEST_CLEANUP_INACTIVE_HOURS", 24)
GUEST_CLEANUP_BATCH_SIZE = _int("GUEST_CLEANUP_BATCH_SIZE", 500)
GUEST_CLEANUP_MAX_BATCHES = _int("GUEST_CLEANUP_MAX_BATCHES", 20)

# ============================================================================
# Shared cache (MySQL-only). Redis is not supported.
# ============================================================================
# mysql  — authoritative shared cache across gunicorn workers (required for multi-worker)
# memory — process-local only; bounded non-authoritative optimization for single-process dev
SUPPORTED_CACHE_BACKENDS = frozenset({"mysql", "memory"})
SUPPORTED_SEMANTIC_CACHE_BACKENDS = frozenset({"mysql", "memory"})

CACHE_BACKEND = _env("CACHE_BACKEND", "mysql").strip().lower()
# REDIS_URL is obsolete and ignored. Kept only so old .env files do not break parsing.
REDIS_URL = _env("REDIS_URL", "")
CACHE_TTL_MINUTES = _int("CACHE_TTL_MINUTES", 60)
CACHE_MAX_SESSIONS = _int("CACHE_MAX_SESSIONS", 100)
CACHE_KEY_PREFIX = _env("CACHE_KEY_PREFIX", "rethinkai:chatcache:")

SEMANTIC_CACHE_ENABLED = _truthy("SEMANTIC_CACHE_ENABLED", "false")
_semantic_default_backend = "mysql" if CACHE_BACKEND == "mysql" else "memory"
SEMANTIC_CACHE_BACKEND = _env(
    "SEMANTIC_CACHE_BACKEND", _semantic_default_backend
).strip().lower()
SEMANTIC_CACHE_KEY = _env("SEMANTIC_CACHE_KEY", "rethinkai:semantic_cache:entries")
SEMANTIC_CACHE_THRESHOLD = _float("SEMANTIC_CACHE_THRESHOLD", 0.95)
SEMANTIC_CACHE_TTL_SECONDS = _int("SEMANTIC_CACHE_TTL_SECONDS", 900)
SEMANTIC_CACHE_MAX = _int("SEMANTIC_CACHE_MAX", 200)


def validate_cache_backend_contract() -> list[str]:
    """Reject Redis and other unsupported shared-state backends."""
    errors: list[str] = []
    if CACHE_BACKEND == "redis":
        errors.append(
            "CACHE_BACKEND=redis is not supported. "
            "Use CACHE_BACKEND=mysql for shared cross-worker state "
            "(or memory for single-process local optimization only)."
        )
    elif CACHE_BACKEND not in SUPPORTED_CACHE_BACKENDS:
        errors.append(
            f"CACHE_BACKEND={CACHE_BACKEND!r} is not supported. "
            "Use 'mysql' or 'memory'."
        )
    if SEMANTIC_CACHE_BACKEND == "redis":
        errors.append(
            "SEMANTIC_CACHE_BACKEND=redis is not supported. "
            "Use SEMANTIC_CACHE_BACKEND=mysql "
            "(or memory for single-process local optimization only)."
        )
    elif SEMANTIC_CACHE_BACKEND not in SUPPORTED_SEMANTIC_CACHE_BACKENDS:
        errors.append(
            f"SEMANTIC_CACHE_BACKEND={SEMANTIC_CACHE_BACKEND!r} is not supported. "
            "Use 'mysql' or 'memory'."
        )
    return errors


def assert_cache_backend_contract() -> None:
    errors = validate_cache_backend_contract()
    if errors:
        raise RuntimeError("; ".join(errors))

# ============================================================================
# Civic chatbot / execution pipeline
# ============================================================================
PREPARED_SQL_ENABLED = _truthy("PREPARED_SQL_ENABLED", "true")
PREPARED_SQL_USE_LLM_SUMMARY = _truthy("PREPARED_SQL_USE_LLM_SUMMARY", "false")
PREPARED_SQL_CONVERSATIONAL = _truthy("PREPARED_SQL_CONVERSATIONAL", "true")
PREPARED_SQL_MULTI_PART = _truthy("PREPARED_SQL_MULTI_PART", "true")
PREPARED_SQL_311_INCLUDE_TYPES = _truthy("PREPARED_SQL_311_INCLUDE_TYPES", "true")

EXECUTION_PIPELINE_ENABLED = _truthy("EXECUTION_PIPELINE_ENABLED", "true")
EXECUTION_LLM_PLANNER_ENABLED = _truthy("EXECUTION_LLM_PLANNER_ENABLED", "true")
EXECUTION_PIPELINE_LOG = _truthy("EXECUTION_PIPELINE_LOG", "true")
EXECUTION_PIPELINE_MAX_WORKERS = _int("EXECUTION_PIPELINE_MAX_WORKERS", 4)
ANSWER_SYNTHESIS_ENABLED = _truthy("ANSWER_SYNTHESIS_ENABLED", "true")
FAST_FOLLOWUP_GATE_ENABLED = _truthy("FAST_FOLLOWUP_GATE_ENABLED", "true")
CONVERSATION_RELATIONSHIP_SHADOW_ENABLED = _truthy(
    "CONVERSATION_RELATIONSHIP_SHADOW_ENABLED", "true",
)
CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED = _truthy(
    "CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED", "false",
)
CONVERSATION_DIMENSIONS_SHADOW_ENABLED = _truthy(
    "CONVERSATION_DIMENSIONS_SHADOW_ENABLED", "true",
)
CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED = _truthy(
    "CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED", "false",
)

INTENT_ROUTER_V2_ENABLED = _truthy("INTENT_ROUTER_V2_ENABLED", "true")
ROUTER_RULE_CONFIDENCE_THRESHOLD = _float("ROUTER_RULE_CONFIDENCE_THRESHOLD", 0.75)
BOSTON_GOV_ASYNC_ENABLED = _truthy("BOSTON_GOV_ASYNC_ENABLED", "true")
BOSTON_GOV_BLOCKING = _truthy("BOSTON_GOV_BLOCKING", "false")
BOSTON_GOV_ASYNC_TIMEOUT_SECONDS = _float("BOSTON_GOV_ASYNC_TIMEOUT_SECONDS", 8.0)
CHAT_VECTOR_WRITES_ENABLED = _truthy("CHAT_VECTOR_WRITES_ENABLED", "false")
ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED = _truthy(
    "ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED",
    "false" if _truthy("PRODUCTION_MODE", "false") else "true",
)
DYNAMIC_SQL_ENABLED = _truthy("DYNAMIC_SQL_ENABLED", "false")
REQUEST_BUDGET_SECONDS = _float("REQUEST_BUDGET_SECONDS", 30.0)

PRODUCTION_MODE = _truthy("PRODUCTION_MODE", "false")
INGEST_STALE_HOURS = max(1, _int("INGEST_STALE_HOURS", 26))
REQUEST_METRICS_ENABLED = _truthy("REQUEST_METRICS_ENABLED", "true")
STREAMING_ENABLED = _truthy("STREAMING_ENABLED", "true")

# ============================================================================
# Gemini
# ============================================================================
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_SUMMARY_MODEL = _env("GEMINI_SUMMARY_MODEL", GEMINI_MODEL)
GEMINI_FAST_MODEL = _env("GEMINI_FAST_MODEL", GEMINI_MODEL)
GEMINI_FALLBACK_MODEL = _env("GEMINI_FALLBACK_MODEL", "gemini-2.5-pro")
GEMINI_EMBED_MODEL = _env("GEMINI_EMBED_MODEL", "models/gemini-embedding-001")
GEMINI_REQUEST_TIMEOUT = _int("GEMINI_REQUEST_TIMEOUT", 25)
# Streaming calls may wait longer for the first token on slow hosts / large prompts.
GEMINI_STREAM_TIMEOUT = _int("GEMINI_STREAM_TIMEOUT", 30)
GEMINI_MAX_ATTEMPTS = _int("GEMINI_MAX_ATTEMPTS", 2)

# ============================================================================
# Embeddings / vector stores
# ============================================================================
EMBED_PROVIDER = _env("EMBED_PROVIDER", "gemini").strip().lower()
LOCAL_EMBED_MODEL = _env("LOCAL_EMBED_MODEL", "BAAI/bge-base-en-v1.5")
LOCAL_EMBED_DEVICE = _env("LOCAL_EMBED_DEVICE", "cpu")

VECTOR_DB_PROVIDER = _env("VECTOR_DB_PROVIDER", "chroma").strip().lower()
BM25_HYBRID_ENABLED = _truthy("BM25_HYBRID_ENABLED", "true")
QDRANT_MODE = _env("QDRANT_MODE", "local").strip().lower()
QDRANT_URL = _env("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_PATH = _env("QDRANT_PATH", "../qdrant_data")
QDRANT_COLLECTION = _env("QDRANT_COLLECTION", "otp_documents")
QDRANT_STAGING_COLLECTION = _env("QDRANT_STAGING_COLLECTION", "otp_documents_staging")
QDRANT_INGEST_STAGING = _truthy("QDRANT_INGEST_STAGING", "false")
VECTOR_SIZE = _int("VECTOR_SIZE", 768)

VECTORDB_DIR = _resolve_vectordb(_env("VECTORDB_DIR", "vectordb_new"), "vectordb_new")
VECTORDB_BGE_DIR = _resolve_vectordb(_env("VECTORDB_BGE_DIR", "vectordb_bge_v1"), "vectordb_bge_v1")
ACTIVE_VECTORDB_DIR = VECTORDB_BGE_DIR if EMBED_PROVIDER == "local" else VECTORDB_DIR

PARALLEL_RETRIEVAL = _truthy("PARALLEL_RETRIEVAL", "true")
VECTORDB_SINGLETON = _truthy("VECTORDB_SINGLETON", "true")
CHROMA_MAX_ATTEMPTS = _int("CHROMA_MAX_ATTEMPTS", 2)

# ============================================================================
# MySQL
# ============================================================================
MYSQL_HOST = _env("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _int("MYSQL_PORT", 3306)
MYSQL_USER = _env("MYSQL_USER", "root")
MYSQL_PASSWORD = _env("MYSQL_PASSWORD", "")
MYSQL_DB = _env("MYSQL_DB", "rethink_ai_boston")
MYSQL_POOL_SIZE = max(1, min(32, _int("MYSQL_POOL_SIZE", 6)))
MYSQL_CONNECT_TIMEOUT = _int("MYSQL_CONNECT_TIMEOUT", 10)
MYSQL_MAX_RETRIES = _int("MYSQL_MAX_RETRIES", 3)
MYSQL_POOL_RECYCLE = _int("MYSQL_POOL_RECYCLE", 3600)
# mysql-connector raises PoolError immediately when the per-process pool is empty
# (non-blocking checkout). This bound documents reconnect/ping budget expectations.
MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS = max(
    0.5, _float("MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS", 5.0)
)
# Bounded wait when a duplicate request polls for an in-flight turn to complete.
TURN_DUPLICATE_WAIT_SECONDS = max(0.5, min(10.0, _float("TURN_DUPLICATE_WAIT_SECONDS", 3.0)))

# Legacy POST /chat: unsupported for the public web client. Default off in production.
LEGACY_CHAT_ENABLED = _truthy(
    "LEGACY_CHAT_ENABLED",
    "false" if _truthy("PRODUCTION_MODE", "false") else "true",
)
# Modern conversation endpoints: require client_turn_id in production by default.
REQUIRE_CLIENT_TURN_ID = _truthy(
    "REQUIRE_CLIENT_TURN_ID",
    "true" if _truthy("PRODUCTION_MODE", "false") else "false",
)

MATERIALIZED_VIEWS_ENABLED = _truthy("MATERIALIZED_VIEWS_ENABLED", "true")
EVENTS_MATERIALIZED_DAYS = max(7, min(_int("EVENTS_MATERIALIZED_DAYS", 90), 180))
INGEST_QUEUE_ENABLED = _truthy("INGEST_QUEUE_ENABLED", "true")
BOSTON_311_STALE_HOURS = _float("BOSTON_311_STALE_HOURS", 2.0)
# Beta display thresholds for structured civic freshness (calendar days, America/New_York).
SOURCE_FRESHNESS_311_FRESH_DAYS = max(0, _int("SOURCE_FRESHNESS_311_FRESH_DAYS", 3))
SOURCE_FRESHNESS_311_DELAYED_DAYS = max(
    SOURCE_FRESHNESS_311_FRESH_DAYS, _int("SOURCE_FRESHNESS_311_DELAYED_DAYS", 7)
)
SOURCE_FRESHNESS_CRIME_FRESH_DAYS = max(0, _int("SOURCE_FRESHNESS_CRIME_FRESH_DAYS", 3))
SOURCE_FRESHNESS_CRIME_DELAYED_DAYS = max(
    SOURCE_FRESHNESS_CRIME_FRESH_DAYS, _int("SOURCE_FRESHNESS_CRIME_DELAYED_DAYS", 7)
)
INGEST_QUEUE_KEY = _env("INGEST_QUEUE_KEY", "rethinkai:ingest:jobs")
INGEST_STATUS_PREFIX = _env("INGEST_STATUS_PREFIX", "rethinkai:ingest:status:")
INGEST_STATUS_TTL_SECONDS = _int("INGEST_STATUS_TTL_SECONDS", 604800)

# ============================================================================
# Data ingestion (Google Drive, Gmail, dotnews)
# ============================================================================
GOOGLE_DRIVE_FOLDER_ID = _env("GOOGLE_DRIVE_FOLDER_ID", "")
GOOGLE_CREDENTIALS_PATH = _resolve_path(
    _env("GOOGLE_CREDENTIALS_PATH", "service_account_credentials.json"),
    DATA_INGESTION_DIR,
)

EMAIL_ADDRESS = _env("NEWSLETTER_EMAIL_ADDRESS", "")
GMAIL_CREDENTIALS_PATH = _resolve_path(
    _env("GMAIL_CREDENTIALS_PATH", "gmail_credentials.json"),
    DATA_INGESTION_DIR,
)
GMAIL_TOKEN_PATH = _resolve_path(
    _env("GMAIL_TOKEN_PATH", "gmail_token.json"),
    DATA_INGESTION_DIR,
)

IMAP_SERVER = _env("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = _int("IMAP_PORT", 993)

TEMP_DOWNLOAD_DIR = _resolve_path(
    _env("TEMP_DOWNLOAD_DIR", "./temp_downloads"),
    DATA_INGESTION_DIR,
)
TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

EMAIL_LOOKBACK_DAYS = _int("EMAIL_LOOKBACK_DAYS", 7)
MAX_FILES_PER_RUN = _int("MAX_FILES_PER_RUN", 100)
LLM_MAX_WORKERS = _int("LLM_MAX_WORKERS", 3)
VERBOSE_LOGGING = _truthy("VERBOSE_LOGGING", "false")

SYNC_STATE_FILE = DATA_INGESTION_DIR / ".sync_state.json"
EMAIL_SYNC_STATE_FILE = DATA_INGESTION_DIR / ".email_sync_state.json"
DOTNEWS_SYNC_STATE_FILE = DATA_INGESTION_DIR / ".sync_state_dotnews.json"

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx", ".txt", ".md"})

BOSTON_CKAN_API = _env("BOSTON_CKAN_API", "https://data.boston.gov/api/3/action")

# ============================================================================
# Gunicorn (read by api/gunicorn_conf.py)
# ============================================================================
GUNICORN_BIND = _env("GUNICORN_BIND", f"{HOST}:{PORT}")
GUNICORN_WORKERS = _int("GUNICORN_WORKERS", 2)
GUNICORN_THREADS = _int("GUNICORN_THREADS", 4)
GUNICORN_TIMEOUT = _int("GUNICORN_TIMEOUT", 75)
GUNICORN_WORKER_CLASS = _env("GUNICORN_WORKER_CLASS", "gthread")

# ============================================================================
# LangSmith (optional)
# ============================================================================
LANGCHAIN_TRACING_V2 = _truthy("LANGCHAIN_TRACING_V2", "false")
LANGSMITH_TRACING = _truthy("LANGSMITH_TRACING", "false")
LANGCHAIN_API_KEY = _env("LANGCHAIN_API_KEY", "")
LANGSMITH_API_KEY = _env("LANGSMITH_API_KEY", "")
LANGCHAIN_PROJECT = _env("LANGCHAIN_PROJECT", "rethinkai-community-sentiment")
LANGSMITH_PROJECT = _env("LANGSMITH_PROJECT", "rethinkai-community-sentiment")


def mysql_config(*, charset: str = "utf8mb4") -> dict:
    """Dict suitable for pymysql.connect and similar callers."""
    return {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "database": MYSQL_DB,
        "charset": charset,
    }


# ============================================================================
# Validation / preflight
# ============================================================================
def validate_config(test_connections: bool = False) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []

    errors.extend(validate_cache_backend_contract())

    if not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY is not set")

    if not MYSQL_HOST:
        errors.append("MYSQL_HOST is not set")

    if not MYSQL_USER:
        errors.append("MYSQL_USER is not set")

    if not MYSQL_PASSWORD:
        warnings.append("MYSQL_PASSWORD is empty (may be intentional for local dev)")

    if GOOGLE_DRIVE_FOLDER_ID:
        if not GOOGLE_CREDENTIALS_PATH.exists():
            errors.append(f"Google credentials file not found: {GOOGLE_CREDENTIALS_PATH}")
    else:
        warnings.append("GOOGLE_DRIVE_FOLDER_ID is not set (Drive ingestion disabled)")

    if EMAIL_ADDRESS and not GMAIL_CREDENTIALS_PATH.exists():
        warnings.append(f"Gmail OAuth credentials file not found: {GMAIL_CREDENTIALS_PATH}")

    if GMAIL_TOKEN_PATH and not GMAIL_TOKEN_PATH.exists():
        warnings.append(f"Gmail token not found: {GMAIL_TOKEN_PATH}")

    for name, path in (("VECTORDB_DIR", ACTIVE_VECTORDB_DIR), ("TEMP_DOWNLOAD_DIR", TEMP_DOWNLOAD_DIR)):
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                log_success(f"Created {name}: {path}")
            except OSError as exc:
                errors.append(f"Cannot create {name} '{path}': {exc}")
        elif not os.access(path, os.W_OK):
            errors.append(f"{name} is not writable: {path}")

    if test_connections:
        log_info("Testing connections...")
        mysql_error = _test_mysql_connection()
        if mysql_error:
            errors.append(f"MySQL: {mysql_error}")

        gemini_error = _test_gemini_connection()
        if gemini_error:
            errors.append(f"Gemini API: {gemini_error}")

        if GOOGLE_DRIVE_FOLDER_ID:
            gdrive_error = _test_google_drive_connection()
            if gdrive_error:
                errors.append(f"Google Drive: {gdrive_error}")

        gmail_result = _test_gmail_connection()
        if gmail_result:
            if gmail_result.startswith("AUTH_REQUIRED:"):
                warnings.append(f"Gmail: {gmail_result.replace('AUTH_REQUIRED:', '').strip()}")
            else:
                errors.append(f"Gmail: {gmail_result}")

    for warning in warnings:
        log_warning(warning)

    return errors


def _test_mysql_connection() -> Optional[str]:
    if not MYSQL_HOST or not MYSQL_USER:
        return "Host or user not configured"
    try:
        import pymysql

        conn = pymysql.connect(
            **mysql_config(),
            connect_timeout=10,
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        conn.close()
        log_success(f"MySQL: Connected to {MYSQL_HOST}/{MYSQL_DB}")
        return None
    except ImportError:
        return "pymysql not installed"
    except Exception as exc:
        msg = str(exc)
        if "Access denied" in msg:
            return f"Access denied for user '{MYSQL_USER}'"
        if "Can't connect" in msg or "Connection refused" in msg:
            return f"Cannot connect to {MYSQL_HOST}:{MYSQL_PORT}"
        if "Unknown database" in msg:
            return f"Database '{MYSQL_DB}' does not exist"
        return f"Connection failed: {msg}"


def _test_gemini_connection() -> Optional[str]:
    if not GEMINI_API_KEY:
        return "API key not configured"
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            "Reply with only: All Good!",
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=10,
            ),
        )
        text = (response.text or "").strip()
        if text:
            log_success(f"Gemini API: model '{GEMINI_MODEL}' responded")
            return None
        return "API responded but returned empty content"
    except ImportError:
        return "google-generativeai not installed"
    except Exception as exc:
        msg = str(exc)
        if "API_KEY_INVALID" in msg or "401" in msg or "API key not valid" in msg:
            return "Invalid API key"
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            log_success("Gemini API: key valid (rate limited)")
            return None
        if "not found" in msg.lower() or "404" in msg:
            return f"Model '{GEMINI_MODEL}' not found"
        return f"API call failed: {msg}"


def _test_google_drive_connection() -> Optional[str]:
    if not GOOGLE_CREDENTIALS_PATH.exists():
        return "Credentials file not found"
    try:
        from google.oauth2.service_account import Credentials as ServiceAccountCredentials
        from googleapiclient.discovery import build

        creds = ServiceAccountCredentials.from_service_account_file(
            str(GOOGLE_CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        service = build("drive", "v3", credentials=creds)
        folder = service.files().get(
            fileId=GOOGLE_DRIVE_FOLDER_ID,
            fields="id, name",
        ).execute()
        log_success(f"Google Drive: folder '{folder.get('name', 'unknown')}'")
        return None
    except ImportError:
        return "google-auth or google-api-python-client not installed"
    except Exception as exc:
        msg = str(exc)
        if "404" in msg:
            return f"Folder not found or not shared: {GOOGLE_DRIVE_FOLDER_ID}"
        if "403" in msg:
            return "Permission denied — check service account access"
        return f"Connection failed: {msg}"


def _test_gmail_connection() -> Optional[str]:
    if not GMAIL_CREDENTIALS_PATH.exists():
        return "OAuth credentials file not found"
    if not GMAIL_TOKEN_PATH.exists():
        return "AUTH_REQUIRED: Token not found — run Gmail auth flow"
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(
            str(GMAIL_TOKEN_PATH),
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GMAIL_TOKEN_PATH.write_text(creds.to_json())
        elif creds.expired:
            return "AUTH_REQUIRED: Token expired — re-authenticate"

        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        log_success(f"Gmail: connected as {profile.get('emailAddress', 'unknown')}")
        return None
    except ImportError:
        return "google-auth or google-api-python-client not installed"
    except Exception as exc:
        msg = str(exc)
        if "invalid_grant" in msg.lower() or "401" in msg:
            return "AUTH_REQUIRED: Token invalid — re-authenticate"
        return f"Connection failed: {msg}"


def print_config_summary(test_connections: bool = False) -> bool:
    log("=" * 80)
    log(f"Configuration Summary  [PRODUCTION_MODE={PRODUCTION_MODE}]")
    log("=" * 80)
    log(f"Project root: {PROJECT_ROOT}")
    log(f"App URL: {APP_BASE_URL}  API URL: {API_BASE_URL}")
    log(f"MySQL: {MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}")
    log(f"API bind: {HOST}:{PORT}  production_mode={PRODUCTION_MODE}")
    log(f"Cache: {CACHE_BACKEND}  CORS: {', '.join(ALLOWED_ORIGINS)}")
    log(f"Embeddings: {EMBED_PROVIDER}  active_vectordb={ACTIVE_VECTORDB_DIR}")
    log(f"Gemini: {GEMINI_MODEL}")
    log(f"Ingestion temp dir: {TEMP_DOWNLOAD_DIR}")
    log("=" * 80)

    errors = validate_config(test_connections=test_connections)
    if errors:
        log_error("Configuration errors:")
        for error in errors:
            log_error(f"  {error}")
        return False

    log_success("Configuration valid")
    if test_connections:
        log_success("Connection checks passed")
    return True


class Config:
    """Namespace used by api/api_v2.py and other API modules."""

    API_VERSION = API_VERSION
    RETHINKAI_API_KEYS = RETHINKAI_API_KEYS
    HOST = HOST
    PORT = PORT
    DEBUG = DEBUG
    CACHE_BACKEND = CACHE_BACKEND
    REDIS_URL = REDIS_URL
    CACHE_TTL_MINUTES = CACHE_TTL_MINUTES
    CACHE_MAX_SESSIONS = CACHE_MAX_SESSIONS
    CACHE_KEY_PREFIX = CACHE_KEY_PREFIX
    SEMANTIC_CACHE_ENABLED = SEMANTIC_CACHE_ENABLED
    SEMANTIC_CACHE_THRESHOLD = SEMANTIC_CACHE_THRESHOLD
    SEMANTIC_CACHE_TTL_SECONDS = SEMANTIC_CACHE_TTL_SECONDS
    SEMANTIC_CACHE_MAX = SEMANTIC_CACHE_MAX
    SEMANTIC_CACHE_BACKEND = SEMANTIC_CACHE_BACKEND
    SEMANTIC_CACHE_KEY = SEMANTIC_CACHE_KEY
    PREPARED_SQL_ENABLED = PREPARED_SQL_ENABLED
    FAST_FOLLOWUP_GATE_ENABLED = FAST_FOLLOWUP_GATE_ENABLED
    CONVERSATION_RELATIONSHIP_SHADOW_ENABLED = CONVERSATION_RELATIONSHIP_SHADOW_ENABLED
    CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED = (
        CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED
    )
    CONVERSATION_DIMENSIONS_SHADOW_ENABLED = CONVERSATION_DIMENSIONS_SHADOW_ENABLED
    CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED = (
        CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED
    )
    INTENT_ROUTER_V2_ENABLED = INTENT_ROUTER_V2_ENABLED
    ROUTER_RULE_CONFIDENCE_THRESHOLD = ROUTER_RULE_CONFIDENCE_THRESHOLD
    BOSTON_GOV_ASYNC_ENABLED = BOSTON_GOV_ASYNC_ENABLED
    BOSTON_GOV_BLOCKING = BOSTON_GOV_BLOCKING
    BOSTON_GOV_ASYNC_TIMEOUT_SECONDS = BOSTON_GOV_ASYNC_TIMEOUT_SECONDS
    CHAT_VECTOR_WRITES_ENABLED = CHAT_VECTOR_WRITES_ENABLED
    ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED = ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED
    DYNAMIC_SQL_ENABLED = DYNAMIC_SQL_ENABLED
    REQUEST_BUDGET_SECONDS = REQUEST_BUDGET_SECONDS
    EMBED_PROVIDER = EMBED_PROVIDER
    LOCAL_EMBED_MODEL = LOCAL_EMBED_MODEL
    LOCAL_EMBED_DEVICE = LOCAL_EMBED_DEVICE
    VECTOR_DB_PROVIDER = VECTOR_DB_PROVIDER
    BM25_HYBRID_ENABLED = BM25_HYBRID_ENABLED
    QDRANT_MODE = QDRANT_MODE
    QDRANT_URL = QDRANT_URL
    QDRANT_COLLECTION = QDRANT_COLLECTION
    MATERIALIZED_VIEWS_ENABLED = MATERIALIZED_VIEWS_ENABLED
    INGEST_QUEUE_ENABLED = INGEST_QUEUE_ENABLED
    BOSTON_311_STALE_HOURS = BOSTON_311_STALE_HOURS
    SOURCE_FRESHNESS_311_FRESH_DAYS = SOURCE_FRESHNESS_311_FRESH_DAYS
    SOURCE_FRESHNESS_311_DELAYED_DAYS = SOURCE_FRESHNESS_311_DELAYED_DAYS
    SOURCE_FRESHNESS_CRIME_FRESH_DAYS = SOURCE_FRESHNESS_CRIME_FRESH_DAYS
    SOURCE_FRESHNESS_CRIME_DELAYED_DAYS = SOURCE_FRESHNESS_CRIME_DELAYED_DAYS
    PRODUCTION_MODE = PRODUCTION_MODE
    INGEST_STALE_HOURS = INGEST_STALE_HOURS
    REQUEST_METRICS_ENABLED = REQUEST_METRICS_ENABLED
    GEMINI_FALLBACK_MODEL = GEMINI_FALLBACK_MODEL
    STREAMING_ENABLED = STREAMING_ENABLED
    SECRET_KEY = SECRET_KEY
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE
    AUTH_SESSION_COOKIE_NAME = AUTH_SESSION_COOKIE_NAME
    AUTH_CSRF_COOKIE_NAME = AUTH_CSRF_COOKIE_NAME
    SESSION_MAX_AGE_DAYS = SESSION_MAX_AGE_DAYS
    SESSION_SAMESITE = SESSION_SAMESITE
    GUEST_SESSION_HOURS = GUEST_SESSION_HOURS
    SESSION_TOUCH_DEBOUNCE_SECONDS = SESSION_TOUCH_DEBOUNCE_SECONDS
    RATE_LIMIT_ENABLED = RATE_LIMIT_ENABLED
    RATE_LIMIT_KEY_PREFIX = RATE_LIMIT_KEY_PREFIX
    RATE_LIMIT_GUEST_CREATE_PER_IP = RATE_LIMIT_GUEST_CREATE_PER_IP
    RATE_LIMIT_GUEST_CREATE_WINDOW_SECONDS = RATE_LIMIT_GUEST_CREATE_WINDOW_SECONDS
    RATE_LIMIT_CHAT_PER_SESSION = RATE_LIMIT_CHAT_PER_SESSION
    RATE_LIMIT_CHAT_WINDOW_SECONDS = RATE_LIMIT_CHAT_WINDOW_SECONDS
    RATE_LIMIT_AUTH_PER_IP = RATE_LIMIT_AUTH_PER_IP
    RATE_LIMIT_AUTH_WINDOW_SECONDS = RATE_LIMIT_AUTH_WINDOW_SECONDS
    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH
    MAX_COMMUNITY_NOTE_LENGTH = MAX_COMMUNITY_NOTE_LENGTH
    APP_BASE_URL = APP_BASE_URL
    API_BASE_URL = API_BASE_URL
    ALLOWED_ORIGINS = ALLOWED_ORIGINS
    MYSQL_HOST = MYSQL_HOST
    MYSQL_PORT = MYSQL_PORT
    MYSQL_USER = MYSQL_USER
    MYSQL_PASSWORD = MYSQL_PASSWORD
    MYSQL_DB = MYSQL_DB
    MYSQL_POOL_SIZE = MYSQL_POOL_SIZE
    MYSQL_CONNECT_TIMEOUT = MYSQL_CONNECT_TIMEOUT
    MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS = MYSQL_POOL_CHECKOUT_TIMEOUT_SECONDS
    TURN_DUPLICATE_WAIT_SECONDS = TURN_DUPLICATE_WAIT_SECONDS
    LEGACY_CHAT_ENABLED = LEGACY_CHAT_ENABLED
    REQUIRE_CLIENT_TURN_ID = REQUIRE_CLIENT_TURN_ID
    LOGIN_WINDOW_MINUTES = LOGIN_WINDOW_MINUTES
    LOGIN_LOCK_THRESHOLD = LOGIN_LOCK_THRESHOLD
    LOGIN_LOCK_MINUTES = LOGIN_LOCK_MINUTES
    AUTH_ADMIN_EMAILS = AUTH_ADMIN_EMAILS


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate project configuration")
    parser.add_argument(
        "--test-connections",
        "-t",
        action="store_true",
        help="Test MySQL, Gemini, Drive, and Gmail connections",
    )
    args = parser.parse_args()
    ok = print_config_summary(test_connections=args.test_connections)
    sys.exit(0 if ok else 1)
