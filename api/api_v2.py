"""
api_v2.py

Authenticated API for the Dorchester community chatbot.
Supports password auth, ephemeral guest sessions, role-based admin access,
and per-user conversation threads backed by MySQL.
"""

from __future__ import annotations

import datetime
import copy
import os
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from flask import Flask, Response, g, has_request_context, jsonify, request, session, stream_with_context
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

from db_migrations import run_migrations
from mysql_cache import MySQLCacheStore
from ops_health import check_ingestion_lag, check_311_feed_balance, validate_production_config
from request_metrics import (
    get_llm_calls,
    get_request_id,
    log_chat_metrics,
    reset_request_metrics,
    resolve_execution_model_name,
)
from rate_limit import RateLimiter
from security import (
    generate_token,
    get_client_ip,
    get_token_secret,
    hash_password,
    hash_token,
    json_dumps,
    json_loads,
    normalize_email,
    normalize_username,
    serialize_user,
    summarize_thread_title,
    utcnow,
    validate_password,
    validate_username,
    verify_password,
)

# Setup paths to import from on_the_porch
_THIS_FILE = Path(__file__).resolve()
_API_DIR = _THIS_FILE.parent
_ROOT_DIR = _API_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))
from config import Config, assert_cache_backend_contract  # noqa: E402

assert_cache_backend_contract()

_ON_THE_PORCH_DIR = _ROOT_DIR / "on_the_porch"

if str(_ON_THE_PORCH_DIR) not in sys.path:
    sys.path.insert(0, str(_ON_THE_PORCH_DIR))

_RAG_DIR = _ON_THE_PORCH_DIR / "rag stuff"
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

from unified_chatbot import (  # noqa: E402
    _answer_from_history,
    _bootstrap_env,
    _check_if_needs_new_data,
    _fix_retrieval_vectordb_path,
    _get_llm_client,
    _is_calendar_question,
    _route_question,
    _run_hybrid,
    _run_rag,
    _run_sql,
    build_retrieval_cache,
    build_retrieval_cache_from_result,
    build_small_talk_result,
    create_empty_cache,
    increment_llm_call_counter,
    is_small_talk,
    apply_post_stream_fallback,
    resolve_effective_question,
    run_execution_pipeline,
    should_apply_boston_gov_fallback,
    stream_agent_response,
    try_boston_gov_enrichment,
    _try_311_advisory_response,
    _try_crime_clarification_response,
)
from event_context import (  # noqa: E402
    EventContextValidationError,
    EventResolution,
    MISSING_EVENT_REFERENCE_MESSAGE,
    ValidatedEventContext,
    build_active_event_response,
    build_selected_event_response,
    resolve_event_by_id,
    resolve_event_context,
    validate_event_ui_context,
)
from conversation_relationship import (  # noqa: E402
    ConversationRelationship,
    EventProperty,
    ReasonCode,
    RelationshipDecision,
    classify_shadow_relationship,
    conservative_fallback,
    detect_event_property,
)
from conversation_dimensions import (  # noqa: E402
    CanonicalTopic,
    ConversationDimensions,
    DimensionContext,
    diagnostic_metrics as dimension_diagnostic_metrics,
    extract_conversation_dimensions,
)
from conversation_state import (  # noqa: E402
    ActiveEntity,
    ConversationState,
    ConversationStateError,
    ProposedTransition,
    ThreadStateEnvelope,
    load_thread_state,
    merge_for_commit,
    serialize_thread_state,
)
from conversation_transitions import (  # noqa: E402
    CacheAction,
    CompletionStatus,
    ConversationTransition,
    StateField,
    TransitionAction,
    TransitionReason,
    classify_completion_status,
    plan_conversation_transition,
    with_transition_diagnostics,
)
from emergency_routing import (  # noqa: E402
    EMERGENCY_RESPONSE,
    EmergencyDecision,
    detect_active_emergency,
)
from event_query_context import (  # noqa: E402
    EventPlanReason,
    EventPlanStatus,
    EventQueryDimension,
    EventQueryPlan,
    build_event_cache,
    build_event_sources as build_structured_event_sources,
    cached_event_result,
    compose_event_answer,
    event_cache_matches,
    is_direct_event_list_request,
    plan_event_query,
    retrieve_events,
)
from sql_chat.app4 import is_generic_events_list_question  # noqa: E402
from intent_router import detect_civic_topic  # noqa: E402
from semantic_cache import (  # noqa: E402
    SemanticResponseCache,
    build_semantic_cache_backend,
)

try:
    from ingest_community_notes import ingest_community_notes as _ingest_community_notes  # noqa: E402
except Exception as _ingest_import_err:
    _ingest_community_notes = None
    print(f"Warning: could not import ingest_community_notes: {_ingest_import_err}")

try:
    import chromadb as _chromadb
    import retrieval as _retrieval
except Exception as _chroma_import_err:
    _chromadb = None
    _retrieval = None
    print(f"Warning: could not import chromadb/retrieval: {_chroma_import_err}")

_bootstrap_env()
_fix_retrieval_vectordb_path()


def _admin_vector_writes_enabled() -> bool:
    default = "false" if Config.PRODUCTION_MODE else "true"
    return os.getenv("ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED", default).strip().lower() in (
        "1", "true", "yes",
    )


def _trigger_ingest() -> None:
    """Run ingest_community_notes in a daemon thread so it doesn't block the response."""
    if not _admin_vector_writes_enabled():
        print("Community notes vector ingest skipped (ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED=false)")
        return
    if _ingest_community_notes is None:
        return

    def _run():
        try:
            stats = _ingest_community_notes()
            print(f"Community notes ingested: {stats}")
        except Exception as exc:
            print(f"Background community notes ingest failed: {exc}")

    threading.Thread(target=_run, daemon=True).start()


def _remove_from_chroma(entry_id: int) -> None:
    """Delete a community note from the active vector DB by its note ID."""
    if not _admin_vector_writes_enabled():
        print("Community note vector removal skipped (ADMIN_KNOWLEDGE_AUTO_INGEST_ENABLED=false)")
        return
    if _retrieval is None:
        return

    def _run():
        note_id = f"community_note_{entry_id}"
        try:
            if Config.VECTOR_DB_PROVIDER == "qdrant":
                from qdrant_store import get_qdrant_store

                get_qdrant_store().delete([note_id])
                print(f"Removed {note_id} from Qdrant")
                return
            if _chromadb is None:
                return
            client = _chromadb.PersistentClient(path=str(_retrieval.VECTORDB_DIR))
            collection = client.get_collection("langchain")
            collection.delete(ids=[note_id])
            print(f"Removed {note_id} from Chroma")
        except Exception as exc:
            print(f"Background vector DB removal failed for note {entry_id}: {exc}")

    threading.Thread(target=_run, daemon=True).start()

# The legacy /chat session cache is provided by a pluggable backend defined
# after Config (see `_session_cache`). Set CACHE_BACKEND=mysql to share it
# across gunicorn workers; it defaults to a thread-safe in-process cache.


DOC_TYPE_DIRS = {
    "policy": urljoin(Config.APP_BASE_URL.rstrip("/") + "/", "Policies/"),
    "transcript": "Data/AI meeting transcripts",
    "calendar_event": "Data/newsletters",
    "boston_gov_answer": "https://www.boston.gov",
}

_BOSTON_GOV_HOME = "https://www.boston.gov"
_COMMUNITY_TRANSCRIPTS_LABEL = "Community Transcripts"


def _stream_generation_error(exc: Exception) -> str:
    """User-facing message for streamed chat failures."""
    message = f"{exc.__class__.__name__} {exc}".lower()
    if any(marker in message for marker in ("deadline", "timeout", "timed out")):
        return "The answer took too long to generate. Please try again."
    return "Generation failed."


_SSE_KEEPALIVE_INTERVAL = float(os.getenv("SSE_KEEPALIVE_INTERVAL", "5"))


def _iter_stream_agent_with_keepalive(
    message: str,
    conversation_history: List[Dict[str, str]],
    thread_state: Dict[str, Any],
):
    """Run stream_agent_response in a worker thread; emit None while waiting."""
    done = object()
    event_queue: queue.Queue = queue.Queue()
    try:
        from request_budget import current_deadline, use_request_deadline  # noqa: WPS433

        deadline = current_deadline()
    except Exception:
        deadline = None
        use_request_deadline = None  # type: ignore[assignment]

    def _worker() -> None:
        try:
            if use_request_deadline is None:
                for item in stream_agent_response(message, conversation_history, thread_state):
                    event_queue.put(item)
            else:
                with use_request_deadline(deadline):
                    for item in stream_agent_response(message, conversation_history, thread_state):
                        event_queue.put(item)
        except Exception as exc:  # noqa: BLE001
            event_queue.put(("_execution_metrics", {"llm_calls": get_llm_calls()}))
            event_queue.put(exc)
        else:
            event_queue.put(("_execution_metrics", {"llm_calls": get_llm_calls()}))
        finally:
            event_queue.put(done)

    threading.Thread(target=_worker, daemon=True).start()
    while True:
        try:
            item = event_queue.get(timeout=_SSE_KEEPALIVE_INTERVAL)
        except queue.Empty:
            yield None
            continue
        if item is done:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def _log_exception(prefix: str, exc: Exception) -> None:
    """
    Log exceptions without leaking secrets (e.g., DB usernames/passwords).

    We intentionally avoid printing the full exception string, since some
    connector errors include connection context.
    """
    try:
        errno = getattr(exc, "errno", None)
        suffix = f" (errno={errno})" if errno is not None else ""
        print(f"{prefix}: {exc.__class__.__name__}{suffix}", file=sys.stderr)
    except Exception:
        print(f"{prefix}: <error>", file=sys.stderr)


db_pool = MySQLConnectionPool(
    host=Config.MYSQL_HOST,
    port=Config.MYSQL_PORT,
    user=Config.MYSQL_USER,
    password=Config.MYSQL_PASSWORD,
    database=Config.MYSQL_DB,
    pool_name="api_v2_pool",
    pool_size=Config.MYSQL_POOL_SIZE,
    # Reset session state when a connection is returned to the pool so no
    # leftover state (variables, temp tables) leaks between requests.
    pool_reset_session=True,
    connection_timeout=Config.MYSQL_CONNECT_TIMEOUT,
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=Config.SECRET_KEY,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=Config.SESSION_MAX_AGE_DAYS),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=Config.SESSION_COOKIE_SECURE,
    SESSION_COOKIE_SAMESITE=Config.SESSION_SAMESITE,
)

CORS(
    app,
    supports_credentials=True,
    resources={r"/*": {"origins": Config.ALLOWED_ORIGINS}},
    allow_headers=["Content-Type", "X-CSRF-Token", "RethinkAI-API-Key", "Authorization"],
)


def get_db_connection():
    """Check out a *live* connection from the pool.

    MySQL closes idle connections after ``wait_timeout``. A pooled connection
    that has gone stale raises "MySQL server has gone away" (errno 2006/2013)
    on first use — the classic intermittent 500 that happens precisely when
    traffic is *low* (connections sit idle long enough to be dropped).

    We ``ping(reconnect=True)`` before returning so a dead connection is
    transparently revived. If that slot cannot be revived, we discard it and
    take another from the pool.
    """
    conn = db_pool.get_connection()
    try:
        conn.ping(reconnect=True, attempts=2, delay=1)
        return conn
    except Exception:
        # Could not revive this pooled connection; return it and get a fresh one.
        try:
            conn.close()
        except Exception:
            pass
        conn = db_pool.get_connection()
        conn.ping(reconnect=True, attempts=2, delay=1)
        return conn


_mysql_cache_store: Optional[MySQLCacheStore] = None


def _get_mysql_cache_store() -> MySQLCacheStore:
    global _mysql_cache_store
    if _mysql_cache_store is None:
        _mysql_cache_store = MySQLCacheStore(get_db_connection)
    return _mysql_cache_store


def initialize_database() -> None:
    run_migrations(get_db_connection)


initialize_database()

def ensure_interaction_log_columns():
    """Add missing columns to interaction log."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
                       SELECT COLUMN_NAME
                       FROM INFORMATION_SCHEMA.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE()
                       AND TABLE_NAME = 'interaction_log'
                       """)
        existing = {row["COLUMN_NAME"] for row in cursor.fetchall()}
        additions = [
            ("flagged", "ALTER TABLE interaction_log ADD COLUMN flagged BOOLEAN DEFAULT FALSE"),
            ("flag_reason", "ALTER TABLE interaction_log ADD COLUMN flag_reason VARCHAR(100)"),
            ("flag_details", "ALTER TABLE interaction_log ADD COLUMN flag_details TEXT"),
            ("flagged_at", "ALTER TABLE interaction_log ADD COLUMN flagged_at TIMESTAMP NULL"),
            ("moderator_comment", "ALTER TABLE interaction_log ADD COLUMN moderator_comment TEXT"),
            ("resolved", "ALTER TABLE interaction_log ADD COLUMN resolved BOOLEAN DEFAULT FALSE"),
            ("resolved_at", "ALTER TABLE interaction_log ADD COLUMN resolved_at TIMESTAMP NULL"),
        ]
        for col, sql in additions:
            if col not in existing:
                cursor.execute(sql)
        conn.commit()
        print("✓ interaction_log columns ready")
    except Exception as e:
        _log_exception("Warning: Could not update interaction_log columns", e)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def ensure_admin_knowledge_table():
    """Create admin_knowledge table if it doesn't exist."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_knowledge (
                id INT AUTO_INCREMENT PRIMARY KEY,
                content TEXT,
                category VARCHAR(100) default 'general',
                expires_at TIMESTAMP NULL,
                source_flag_id INT,
                added_by VARCHAR(255),
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        print("✓ admin_knowledge table ready")
    except Exception as e:
        _log_exception("Warning: Could not create admin_knowledge table", e)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

ensure_interaction_log_columns()
ensure_admin_knowledge_table()

def _bootstrap_admin_users() -> None:
    if not Config.AUTH_ADMIN_EMAILS:
        return

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        placeholders = ", ".join(["%s"] * len(Config.AUTH_ADMIN_EMAILS))
        cursor = conn.cursor()
        cursor.execute(
            f"""
            UPDATE users
            SET role = 'admin'
            WHERE email IN ({placeholders}) AND role <> 'admin'
            """,
            tuple(sorted(Config.AUTH_ADMIN_EMAILS)),
        )
        conn.commit()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


_bootstrap_admin_users()


class _BaseSessionCache:
    """Pluggable store for the legacy /chat retrieval cache."""

    def get(self, session_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def set(self, session_id: str, cache: Dict[str, Any]) -> None:
        raise NotImplementedError


class _MemorySessionCache(_BaseSessionCache):
    """In-process cache. Thread-safe and bounded by TTL + LRU.

    NOTE: this is per-process and is NOT shared across gunicorn workers. It is
    only a bounded, non-authoritative optimization for single-process local use.
    Multi-worker deployments must use CACHE_BACKEND=mysql.
    """

    def __init__(self, ttl_minutes: int, max_sessions: int) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ttl_minutes = ttl_minutes
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            cache = self._data.get(session_id)
            return cache if cache is not None else create_empty_cache()

    def set(self, session_id: str, cache: Dict[str, Any]) -> None:
        with self._lock:
            self._data[session_id] = cache
            self._evict_locked()

    def _evict_locked(self) -> None:
        if len(self._data) <= self._max_sessions:
            return
        now = datetime.datetime.now()
        stale_keys = []
        for sid, cache in self._data.items():
            timestamp = cache.get("timestamp")
            if not timestamp:
                continue
            try:
                cache_time = datetime.datetime.fromisoformat(timestamp)
            except Exception:
                continue
            if (now - cache_time).total_seconds() / 60 > self._ttl_minutes:
                stale_keys.append(sid)
        for sid in stale_keys:
            self._data.pop(sid, None)
        if len(self._data) > self._max_sessions:
            ordered = sorted(self._data.items(), key=lambda kv: kv[1].get("timestamp", ""))
            overflow = len(self._data) - self._max_sessions
            for sid, _ in ordered[:overflow]:
                self._data.pop(sid, None)


# OBSOLETE / QUARANTINED: Redis session cache. Unreachable under the MySQL-only
# runtime contract. Kept as a named stub so accidental imports fail loudly.
class _RedisSessionCache(_BaseSessionCache):
    """Unreachable Redis session cache stub. Redis is not supported."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise RuntimeError(
            "Redis session cache is not supported. Use CACHE_BACKEND=mysql."
        )

    def get(self, session_id: str) -> Dict[str, Any]:
        raise RuntimeError("Redis session cache is not supported.")

    def set(self, session_id: str, cache: Dict[str, Any]) -> None:
        raise RuntimeError("Redis session cache is not supported.")


class _MySQLSessionCache(_BaseSessionCache):
    """Cross-worker cache backed by MySQL (only supported shared backend)."""

    def __init__(self, store: MySQLCacheStore, ttl_minutes: int, key_prefix: str) -> None:
        self._store = store
        self._ttl_seconds = max(60, ttl_minutes * 60)
        self._key_prefix = key_prefix

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def get(self, session_id: str) -> Dict[str, Any]:
        try:
            raw = self._store.get(self._key(session_id))
            if raw:
                return json_loads(raw, create_empty_cache())
        except Exception as exc:  # noqa: BLE001
            _log_exception("MySQL cache get failed", exc)
        return create_empty_cache()

    def set(self, session_id: str, cache: Dict[str, Any]) -> None:
        try:
            self._store.set(self._key(session_id), json_dumps(cache), self._ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            _log_exception("MySQL cache set failed", exc)


def _build_session_cache() -> _BaseSessionCache:
    """Construct the configured cache backend, falling back to memory on error."""
    if Config.CACHE_BACKEND == "redis":
        raise RuntimeError(
            "CACHE_BACKEND=redis is not supported. Use CACHE_BACKEND=mysql."
        )
    if Config.CACHE_BACKEND == "mysql":
        try:
            store = _get_mysql_cache_store()
            if store.ping():
                print("Session cache backend: mysql")
                return _MySQLSessionCache(
                    store, Config.CACHE_TTL_MINUTES, Config.CACHE_KEY_PREFIX,
                )
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARNING: mysql cache backend unavailable ({exc.__class__.__name__}); "
                "falling back to in-memory cache.",
                file=sys.stderr,
            )
    return _MemorySessionCache(Config.CACHE_TTL_MINUTES, Config.CACHE_MAX_SESSIONS)


_session_cache = _build_session_cache()


def _build_rate_limiter() -> RateLimiter:
    mysql_store = None
    if Config.CACHE_BACKEND == "redis":
        raise RuntimeError(
            "CACHE_BACKEND=redis is not supported. Use CACHE_BACKEND=mysql."
        )
    if Config.CACHE_BACKEND == "mysql":
        try:
            store = _get_mysql_cache_store()
            if store.ping():
                mysql_store = store
            else:
                _log_exception(
                    "Rate limiter mysql unavailable; using in-memory fallback",
                    RuntimeError("ping failed"),
                )
        except Exception as exc:  # noqa: BLE001
            _log_exception("Rate limiter mysql unavailable; using in-memory fallback", exc)
    return RateLimiter(
        mysql_store=mysql_store,
        key_prefix=Config.RATE_LIMIT_KEY_PREFIX,
    )


_rate_limiter = _build_rate_limiter()


def _semantic_embed(text: str):
    """Embed a question for the semantic cache (same provider as RAG)."""
    import retrieval

    return retrieval.embed_text(text)


def _build_semantic_cache() -> SemanticResponseCache:
    backend_name = Config.SEMANTIC_CACHE_BACKEND
    mysql_store = None
    if backend_name == "redis":
        raise RuntimeError(
            "SEMANTIC_CACHE_BACKEND=redis is not supported. "
            "Use SEMANTIC_CACHE_BACKEND=mysql."
        )
    if backend_name == "mysql":
        try:
            store = _get_mysql_cache_store()
            if not store.ping():
                raise RuntimeError("mysql cache ping failed")
        except Exception as exc:  # noqa: BLE001
            _log_exception(
                "Semantic cache mysql unavailable; using in-memory backend",
                exc,
            )
            backend_name = "memory"
        else:
            mysql_store = store

    backend = build_semantic_cache_backend(
        backend_name=backend_name,
        redis_key=Config.SEMANTIC_CACHE_KEY,
        mysql_store=mysql_store,
    )
    return SemanticResponseCache(
        enabled=Config.SEMANTIC_CACHE_ENABLED,
        threshold=Config.SEMANTIC_CACHE_THRESHOLD,
        ttl_seconds=Config.SEMANTIC_CACHE_TTL_SECONDS,
        max_entries=Config.SEMANTIC_CACHE_MAX,
        embed_fn=_semantic_embed,
        backend=backend,
    )


_semantic_cache = _build_semantic_cache()
if Config.SEMANTIC_CACHE_ENABLED:
    print(
        f"Semantic response cache: enabled "
        f"(backend={Config.SEMANTIC_CACHE_BACKEND}, "
        f"threshold={Config.SEMANTIC_CACHE_THRESHOLD}, "
        f"ttl={Config.SEMANTIC_CACHE_TTL_SECONDS}s)"
    )

print(
    f"Embeddings: provider={Config.EMBED_PROVIDER}, "
    f"model={Config.LOCAL_EMBED_MODEL if Config.EMBED_PROVIDER == 'local' else os.getenv('GEMINI_EMBED_MODEL', 'models/gemini-embedding-001')}"
)
print(
    f"Vector DB: provider={Config.VECTOR_DB_PROVIDER}, "
    f"bm25_hybrid={Config.BM25_HYBRID_ENABLED}"
    + (
        f", qdrant={Config.QDRANT_COLLECTION}"
        if Config.VECTOR_DB_PROVIDER == "qdrant"
        else ""
    )
)


def _run_startup_checks() -> None:
    ok, issues = validate_production_config(
        production_mode=Config.PRODUCTION_MODE,
        cache_backend=Config.CACHE_BACKEND,
        semantic_cache_enabled=Config.SEMANTIC_CACHE_ENABLED,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        gemini_fast_model=os.getenv("GEMINI_FAST_MODEL", "gemini-2.5-flash-lite"),
        embed_provider=Config.EMBED_PROVIDER,
        vectordb_dir=Path(_retrieval.VECTORDB_DIR) if _retrieval else None,
        vector_db_provider=Config.VECTOR_DB_PROVIDER,
    )
    for msg in issues:
        prefix = "ERROR" if ("must be" in msg or "missing" in msg) else "WARNING"
        print(f"{prefix}: Production config: {msg}", file=sys.stderr)
    if not ok:
        raise RuntimeError(
            "Invalid production configuration (PRODUCTION_MODE=true). "
            "Fix CACHE_BACKEND and other settings in .env, then restart."
        )


# Fail fast if Redis (or another unsupported backend) is configured.
if Config.PRODUCTION_MODE:
    _run_startup_checks()
    print("Production mode: enabled", file=sys.stderr)


def _log_agent_metrics(
    *,
    mode: str,
    cache_hit: bool = False,
    thread_id: Optional[str] = None,
    streaming: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not Config.REQUEST_METRICS_ENABLED:
        return
    merged_extra = {"embed_provider": Config.EMBED_PROVIDER}
    req_id = get_request_id()
    if req_id:
        merged_extra["request_id"] = req_id
    if cache_hit:
        merged_extra["cache_layer"] = "semantic"
    if extra:
        merged_extra.update(extra)
        if extra.get("prepared_intent"):
            merged_extra["tier"] = "prepared_sql"
        elif extra.get("router") == "rules":
            merged_extra["tier"] = "rules"
    log_chat_metrics(
        enabled=True,
        mode=mode,
        intent=mode,
        cache_hit=cache_hit,
        thread_id=thread_id,
        streaming=streaming,
        extra=merged_extra,
    )

# Words that make a question time-sensitive; such questions are never served
# from (or stored in) the semantic cache because the right answer changes daily.
_SEMANTIC_TIME_WORDS = (
    "today", "tonight", "yesterday", "tomorrow", "this week", "last week",
    "next week", "this month", "last month", "this year", "recent", "recently",
    "latest", "currently", "right now", "this weekend",
)


def _is_fast_sql_question(message: str) -> bool:
    """Prepared or rule-routed SQL questions should skip semantic-cache embedding."""
    try:
        from sql_chat.prepared_sql import match_prepared_sql  # noqa: WPS433
        import intent_router  # noqa: WPS433

        if match_prepared_sql(message):
            return True
        rule = intent_router.route_with_rules(message)
        return bool(rule and rule.get("mode") == "sql")
    except Exception:
        return False


def _semantic_cache_eligible(message: str, conversation_history: List[Dict[str, str]]) -> bool:
    """Only cache/serve fresh-topic, non-time-sensitive first-turn questions."""
    if not Config.SEMANTIC_CACHE_ENABLED:
        return False
    if conversation_history:
        return False
    if _is_calendar_question(message):
        return False
    if _is_fast_sql_question(message):
        return False
    lowered = (message or "").lower()
    if any(word in lowered for word in _SEMANTIC_TIME_WORDS):
        return False
    return True


def _is_cacheable_semantic_answer(answer: str) -> bool:
    """Do not cache empty RAG failures — they may succeed after ingest or Chroma recovery."""
    normalized = (answer or "").strip().lower()
    if not normalized:
        return False
    if normalized == "no relevant information found.":
        return False
    if normalized.startswith("i couldn't find an answer"):
        return False
    return True

def _cookie_kwargs(*, httponly: bool, expires: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "httponly": httponly,
        "secure": Config.SESSION_COOKIE_SECURE,
        "samesite": Config.SESSION_SAMESITE,
        "path": "/",
    }
    if expires is not None:
        kwargs["expires"] = expires
    return kwargs


def _set_auth_cookies(response, session_token: str, csrf_token: str, expires_at: datetime.datetime) -> None:
    response.set_cookie(
        Config.AUTH_SESSION_COOKIE_NAME,
        session_token,
        **_cookie_kwargs(httponly=True, expires=expires_at),
    )
    response.set_cookie(
        Config.AUTH_CSRF_COOKIE_NAME,
        csrf_token,
        **_cookie_kwargs(httponly=False, expires=expires_at),
    )
    g.csrf_cookie_written = True



def _clear_auth_cookies(response) -> None:
    response.delete_cookie(Config.AUTH_SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(Config.AUTH_CSRF_COOKIE_NAME, path="/")
    g.csrf_cookie_written = True



def _json_error(message: str, status: int, code: Optional[str] = None):
    payload = {"error": message}
    if code:
        payload["code"] = code
    return jsonify(payload), status



@app.errorhandler(HTTPException)
def handle_http_exception(exc: HTTPException):
    return _json_error(exc.description or "Request failed.", exc.code or 500, "http_error")


@app.errorhandler(Exception)
def handle_unhandled_exception(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    _log_exception("Unhandled server error", exc)
    return _json_error("Internal server error.", 500, "internal_error")


def _provider_names(conn, user_id: str) -> List[str]:
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT provider FROM auth_identities WHERE user_id = %s", (user_id,))
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()



def _fetch_user_by_id(conn, user_id: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s LIMIT 1", (user_id,))
        return cursor.fetchone()
    finally:
        cursor.close()



def _fetch_user_by_email(conn, email: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s LIMIT 1", (email,))
        return cursor.fetchone()
    finally:
        cursor.close()



def _fetch_user_by_username(conn, username: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE username = %s LIMIT 1", (username,))
        return cursor.fetchone()
    finally:
        cursor.close()



def _fetch_password_login_row(conn, email: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT ai.id AS identity_id, ai.password_hash, ai.last_used_at,
                   u.id, u.email, u.username, u.role, u.status,
                   u.profile_complete, u.created_at, u.updated_at, u.last_login_at
            FROM auth_identities ai
            JOIN users u ON u.id = ai.user_id
            WHERE ai.provider = 'password' AND u.email = %s
            LIMIT 1
            """,
            (email,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()



def _create_user(
    conn,
    email: str,
    username: str,
    *,
    profile_complete: bool,
    is_guest: bool = False,
) -> Dict[str, Any]:
    user_id = str(uuid.uuid4())
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (id, email, username, role, status, profile_complete, is_guest)
            VALUES (%s, %s, %s, 'user', 'active', %s, %s)
            """,
            (user_id, email, username, profile_complete, is_guest),
        )
    finally:
        cursor.close()
    return _fetch_user_by_id(conn, user_id)


def _create_guest_user(conn) -> Dict[str, Any]:
    short_id = uuid.uuid4().hex[:8]
    email = f"guest-{uuid.uuid4()}@guest.local"
    username = f"guest-{short_id}"
    return _create_user(conn, email, username, profile_complete=True, is_guest=True)


def _should_promote_to_admin(email: str, *, is_guest: bool = False) -> bool:
    if is_guest:
        return False
    return normalize_email(email) in Config.AUTH_ADMIN_EMAILS


def _promote_user_to_admin_if_configured(
    conn,
    *,
    user_id: str,
    email: str,
    current_role: Optional[str] = None,
    audit_event: Optional[str] = None,
    is_guest: bool = False,
) -> bool:
    if not _should_promote_to_admin(email, is_guest=is_guest):
        return False
    if current_role == "admin":
        return False

    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET role = 'admin' WHERE id = %s AND role <> 'admin'",
            (user_id,),
        )
        updated = cursor.rowcount > 0
    finally:
        cursor.close()

    if updated and audit_event and has_request_context():
        _record_auth_event(
            conn,
            audit_event,
            success=True,
            user_id=user_id,
            details={"email": normalize_email(email), "source": "AUTH_ADMIN_EMAILS"},
        )
    return updated



def _create_auth_identity(
    conn,
    *,
    user_id: str,
    provider: str,
    provider_subject: Optional[str] = None,
    password_hash_value: Optional[str] = None,
) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO auth_identities (id, user_id, provider, provider_subject, password_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), user_id, provider, provider_subject, password_hash_value),
        )
    finally:
        cursor.close()



def _update_user_login_stamp(conn, user_id: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET last_login_at = %s WHERE id = %s",
            (utcnow(), user_id),
        )
    finally:
        cursor.close()



def _update_identity_last_used(conn, user_id: str, provider: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE auth_identities SET last_used_at = %s WHERE user_id = %s AND provider = %s",
            (utcnow(), user_id, provider),
        )
    finally:
        cursor.close()



def _update_password_hash(conn, identity_id: str, new_password_hash: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE auth_identities SET password_hash = %s WHERE id = %s",
            (new_password_hash, identity_id),
        )
    finally:
        cursor.close()



def _create_web_session(conn, user_id: str, *, is_guest: bool = False) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())
    session_token = generate_token(32)
    csrf_token = generate_token(24)
    if is_guest:
        expires_at = utcnow() + datetime.timedelta(hours=Config.GUEST_SESSION_HOURS)
    else:
        expires_at = utcnow() + datetime.timedelta(days=Config.SESSION_MAX_AGE_DAYS)
    secret = get_token_secret()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO web_sessions (
                id, user_id, session_token_hash, csrf_token_hash,
                user_agent, ip_created, last_seen_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                user_id,
                hash_token(session_token, secret),
                hash_token(csrf_token, secret),
                request.headers.get("User-Agent", "")[:512],
                get_client_ip(request.headers, request.remote_addr),
                utcnow(),
                expires_at,
            ),
        )
    finally:
        cursor.close()
    return {
        "id": session_id,
        "session_token": session_token,
        "csrf_token": csrf_token,
        "expires_at": expires_at,
        "is_guest": is_guest,
    }



def _revoke_session(conn, session_id: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE web_sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL",
            (utcnow(), session_id),
        )
    finally:
        cursor.close()



def _current_session_row(conn, raw_session_token: str) -> Optional[Dict[str, Any]]:
    secret = get_token_secret()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT ws.id AS session_id, ws.user_id, ws.csrf_token_hash,
                   ws.expires_at, ws.revoked_at, ws.last_seen_at,
                   u.id, u.email, u.username, u.role, u.status,
                   u.profile_complete, u.is_guest,
                   u.created_at, u.updated_at, u.last_login_at
            FROM web_sessions ws
            JOIN users u ON u.id = ws.user_id
            WHERE ws.session_token_hash = %s
            LIMIT 1
            """,
            (hash_token(raw_session_token, secret),),
        )
        return cursor.fetchone()
    finally:
        cursor.close()



def _touch_session(conn, session_id: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE web_sessions SET last_seen_at = %s WHERE id = %s",
            (utcnow(), session_id),
        )
    finally:
        cursor.close()



def _touch_session_if_due(conn, session_row: Dict[str, Any]) -> bool:
    """Update last_seen_at at most once per debounce window."""
    debounce = Config.SESSION_TOUCH_DEBOUNCE_SECONDS
    if debounce <= 0:
        _touch_session(conn, session_row["session_id"])
        return True

    last_seen = session_row.get("last_seen_at")
    if last_seen is not None:
        elapsed = (utcnow() - last_seen).total_seconds()
        if elapsed < debounce:
            return False

    _touch_session(conn, session_row["session_id"])
    return True



def _activate_web_session(
    conn,
    raw_session_token: str,
    *,
    via_bearer: bool = False,
) -> Optional[Dict[str, Any]]:
    session_row = _current_session_row(conn, raw_session_token)
    if not session_row:
        return None

    if session_row.get("revoked_at") or session_row.get("expires_at") <= utcnow():
        _revoke_session(conn, session_row["session_id"])
        conn.commit()
        return None

    providers = _provider_names(conn, session_row["user_id"])
    promoted = False
    if not session_row.get("is_guest"):
        promoted = _promote_user_to_admin_if_configured(
            conn,
            user_id=session_row["user_id"],
            email=session_row["email"],
            current_role=session_row.get("role"),
            audit_event="admin_role_bootstrap",
            is_guest=False,
        )

    touched = _touch_session_if_due(conn, session_row)
    if touched or promoted:
        conn.commit()

    if promoted:
        session_row = _current_session_row(conn, raw_session_token) or session_row

    g.session_row = session_row
    g.current_user_row = session_row
    g.linked_providers = providers
    g.current_user = serialize_user(session_row, providers)
    g.session_id = session_row["session_id"]
    g.bearer_session = via_bearer
    g.is_guest = bool(session_row.get("is_guest"))
    return session_row



def _record_auth_event(
    conn,
    event_type: str,
    *,
    success: bool,
    user_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO auth_audit_log (user_id, event_type, success, ip_address, user_agent, details_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                event_type,
                success,
                get_client_ip(request.headers, request.remote_addr),
                request.headers.get("User-Agent", "")[:512],
                json_dumps(details or {}),
            ),
        )
    finally:
        cursor.close()



def _fetch_login_attempt(conn, email: str, ip_address: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM login_attempts WHERE normalized_email = %s AND ip_address = %s LIMIT 1",
            (email, ip_address),
        )
        return cursor.fetchone()
    finally:
        cursor.close()



def _is_locked(record: Optional[Dict[str, Any]]) -> bool:
    return bool(record and record.get("locked_until") and record["locked_until"] > utcnow())



def _record_failed_login(conn, email: str, ip_address: str) -> Optional[datetime.datetime]:
    now = utcnow()
    record = _fetch_login_attempt(conn, email, ip_address)
    cursor = conn.cursor()
    try:
        if not record:
            failure_count = 1
            locked_until = now + datetime.timedelta(minutes=Config.LOGIN_LOCK_MINUTES) if failure_count >= Config.LOGIN_LOCK_THRESHOLD else None
            cursor.execute(
                """
                INSERT INTO login_attempts (
                    normalized_email, ip_address, failure_count,
                    first_attempt_at, last_attempt_at, locked_until
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (email, ip_address, failure_count, now, now, locked_until),
            )
            return locked_until

        first_attempt = record["first_attempt_at"]
        if first_attempt is None or (now - first_attempt).total_seconds() > Config.LOGIN_WINDOW_MINUTES * 60:
            failure_count = 1
            first_attempt = now
        else:
            failure_count = int(record.get("failure_count", 0)) + 1

        locked_until = now + datetime.timedelta(minutes=Config.LOGIN_LOCK_MINUTES) if failure_count >= Config.LOGIN_LOCK_THRESHOLD else None
        cursor.execute(
            """
            UPDATE login_attempts
            SET failure_count = %s,
                first_attempt_at = %s,
                last_attempt_at = %s,
                locked_until = %s
            WHERE normalized_email = %s AND ip_address = %s
            """,
            (failure_count, first_attempt, now, locked_until, email, ip_address),
        )
        return locked_until
    finally:
        cursor.close()



def _clear_login_attempts(conn, email: str, ip_address: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM login_attempts WHERE normalized_email = %s AND ip_address = %s",
            (email, ip_address),
        )
    finally:
        cursor.close()



def _enforce_csrf():
    if g.get("api_key_authenticated") and not g.get("current_user_row"):
        return None

    header_token = request.headers.get("X-CSRF-Token", "")
    session_row = g.get("session_row")

    if g.get("bearer_session"):
        if not header_token or not session_row:
            return _json_error("CSRF validation failed.", 403, "csrf_failed")
        expected_hash = session_row.get("csrf_token_hash")
        if expected_hash != hash_token(header_token, get_token_secret()):
            return _json_error("CSRF validation failed.", 403, "csrf_failed")
        return None

    cookie_token = request.cookies.get(Config.AUTH_CSRF_COOKIE_NAME, "")
    if not cookie_token or not header_token or cookie_token != header_token:
        return _json_error("CSRF validation failed.", 403, "csrf_failed")

    if session_row:
        expected_hash = session_row.get("csrf_token_hash")
        if expected_hash != hash_token(cookie_token, get_token_secret()):
            return _json_error("CSRF validation failed.", 403, "csrf_failed")

    return None



def _require_user(*, allow_incomplete: bool = False):
    current_user = g.get("current_user_row")
    if not current_user:
        return _json_error("Authentication required.", 401, "auth_required")
    if current_user.get("status") != "active":
        return _json_error("Account is disabled.", 403, "account_disabled")
    if not allow_incomplete and not current_user.get("profile_complete"):
        return _json_error("Complete your profile before using the app.", 409, "profile_incomplete")
    return None



def _require_admin():
    result = _require_user()
    if result:
        return result
    if g.get("current_user_row", {}).get("is_guest"):
        return _json_error("Admin access required.", 403, "guest_not_allowed")
    if g.get("current_user_row", {}).get("role") != "admin":
        return _json_error("Admin access required.", 403, "admin_required")
    return None


def _require_admin_mutation():
    """Admin check plus CSRF for cookie-authenticated mutating requests."""
    result = _require_admin()
    if result:
        return result
    return _enforce_csrf()


def _validate_message(message: str):
    text = (message or "").strip()
    if not text:
        return _json_error("Message is required.", 400, "missing_message")
    if len(text) > Config.MAX_MESSAGE_LENGTH:
        return _json_error(
            f"Message is too long (max {Config.MAX_MESSAGE_LENGTH} characters).",
            400,
            "message_too_long",
        )
    return None


def _verify_log_ownership(
    conn,
    log_id: int,
    *,
    user_id: Optional[str],
    session_id: str,
) -> bool:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT user_id, session_id FROM interaction_log WHERE id = %s",
            (log_id,),
        )
        row = cursor.fetchone()
        if not row:
            return False
        if user_id and row.get("user_id") == user_id:
            return True
        if session_id and row.get("session_id") == session_id:
            if row.get("user_id") is None:
                return True
            return bool(user_id and row.get("user_id") == user_id)
        return False
    finally:
        cursor.close()


def _check_shared_cache_health() -> str:
    if Config.CACHE_BACKEND == "mysql":
        try:
            return "connected" if _get_mysql_cache_store().ping() else "disconnected"
        except Exception:
            return "disconnected"
    if Config.CACHE_BACKEND == "memory":
        return "in_process"
    return "unsupported"


def _check_chroma_health() -> str:
    if Config.VECTOR_DB_PROVIDER == "qdrant":
        return "bypassed"
    if _chromadb is None or _retrieval is None:
        return "unavailable"
    try:
        vdb_dir = Path(_retrieval.VECTORDB_DIR)
        if not vdb_dir.exists():
            return "missing"
        client = _chromadb.PersistentClient(path=str(vdb_dir))
        collection = client.get_collection("langchain")
        count = collection.count()
        return "connected" if count >= 0 else "empty"
    except Exception:
        return "degraded"


def _check_qdrant_health() -> str:
    if Config.VECTOR_DB_PROVIDER != "qdrant":
        return "not_configured"
    try:
        from qdrant_store import check_qdrant_health

        return check_qdrant_health()
    except Exception:
        return "unavailable"



def _rate_limit_or_none(bucket: str, identifier: str, limit: int, window_seconds: int):
    if not Config.RATE_LIMIT_ENABLED:
        return None
    allowed, retry_after = _rate_limiter.check(bucket, identifier, limit, window_seconds)
    if allowed:
        return None
    response = jsonify(
        {
            "error": "Too many requests. Please try again later.",
            "code": "rate_limited",
            "retry_after": retry_after,
        }
    )
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response



def _require_api_key_or_user():
    if g.get("current_user_row") or g.get("api_key_authenticated"):
        return None
    return _json_error("Authentication required.", 401, "auth_required")



def _serialize_thread(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "last_message_at": row["last_message_at"].isoformat() if row.get("last_message_at") else None,
        "archived_at": row["archived_at"].isoformat() if row.get("archived_at") else None,
        "deleted_at": row["deleted_at"].isoformat() if row.get("deleted_at") else None,
        "last_message_preview": row.get("last_message_preview"),
    }



def _serialize_message(row: Dict[str, Any]) -> Dict[str, Any]:
    meta = json_loads(row.get("message_meta_json"), {})
    serialized = {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "role": row["role"],
        "content": row["content"],
        "response_mode": row.get("response_mode"),
        "sources": json_loads(row.get("sources_json"), []),
        "model_name": row.get("model_name"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "log_id": meta.get("log_id"),
    }
    if isinstance(meta.get("resolved_entity"), dict):
        serialized["resolved_entity"] = meta["resolved_entity"]
    return serialized



def _fetch_thread(conn, user_id: str, thread_id: str) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT ct.*,
                   (
                       SELECT content FROM conversation_messages cm
                       WHERE cm.thread_id = ct.id
                       ORDER BY cm.created_at DESC,
                                CASE cm.role
                                    WHEN 'assistant' THEN 0
                                    WHEN 'user' THEN 1
                                    ELSE 2
                                END,
                                cm.id DESC
                       LIMIT 1
                   ) AS last_message_preview
            FROM conversation_threads ct
            WHERE ct.id = %s AND ct.user_id = %s AND ct.deleted_at IS NULL
            LIMIT 1
            """,
            (thread_id, user_id),
        )
        return cursor.fetchone()
    finally:
        cursor.close()



def _list_threads(conn, user_id: str) -> List[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT ct.*,
                   (
                       SELECT content FROM conversation_messages cm
                       WHERE cm.thread_id = ct.id
                       ORDER BY cm.created_at DESC,
                                CASE cm.role
                                    WHEN 'assistant' THEN 0
                                    WHEN 'user' THEN 1
                                    ELSE 2
                                END,
                                cm.id DESC
                       LIMIT 1
                   ) AS last_message_preview
            FROM conversation_threads ct
            WHERE ct.user_id = %s AND ct.deleted_at IS NULL
            ORDER BY CASE WHEN ct.archived_at IS NULL THEN 0 ELSE 1 END,
                     ct.last_message_at DESC,
                     ct.created_at DESC
            """,
            (user_id,),
        )
        return cursor.fetchall()
    finally:
        cursor.close()



def _create_thread(conn, user_id: str, title: Optional[str]) -> Dict[str, Any]:
    thread_id = str(uuid.uuid4())
    final_title = title.strip() if title and title.strip() else "New conversation"
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO conversation_threads (id, user_id, title, thread_state_json, last_message_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                thread_id,
                user_id,
                final_title,
                serialize_thread_state(
                    ThreadStateEnvelope(retrieval_cache=create_empty_cache())
                ),
                utcnow(),
            ),
        )
    finally:
        cursor.close()
    return _fetch_thread(conn, user_id, thread_id)



def _update_thread(conn, thread_id: str, *, title: Optional[str] = None, archived: Optional[bool] = None) -> None:
    updates = []
    values: List[Any] = []
    if title is not None:
        updates.append("title = %s")
        values.append(title)
    if archived is not None:
        updates.append("archived_at = %s")
        values.append(utcnow() if archived else None)
    if not updates:
        return
    values.append(thread_id)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE conversation_threads SET {', '.join(updates)} WHERE id = %s",
            tuple(values),
        )
    finally:
        cursor.close()



def _soft_delete_thread(conn, thread_id: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_threads SET deleted_at = %s WHERE id = %s",
            (utcnow(), thread_id),
        )
    finally:
        cursor.close()



def _fetch_messages(conn, thread_id: str, *, limit: int, before: Optional[str]) -> List[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        params: List[Any] = [thread_id]
        where_clause = "WHERE thread_id = %s"
        if before:
            cursor.execute(
                "SELECT created_at FROM conversation_messages WHERE id = %s AND thread_id = %s LIMIT 1",
                (before, thread_id),
            )
            pivot = cursor.fetchone()
            if pivot and pivot.get("created_at"):
                where_clause += " AND created_at < %s"
                params.append(pivot["created_at"])
        params.append(limit)
        cursor.execute(
            f"""
            SELECT *
            FROM conversation_messages
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            tuple(params),
        )
        rows = cursor.fetchall()
        rows.reverse()
        return rows
    finally:
        cursor.close()



def _fetch_recent_history(conn, thread_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _fetch_messages(conn, thread_id, limit=limit, before=None)



def _insert_message(
    conn,
    *,
    thread_id: str,
    user_id: str,
    role: str,
    content: str,
    response_mode: Optional[str] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    model_name: Optional[str] = None,
    message_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    message_id = str(uuid.uuid4())
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO conversation_messages (
                id, thread_id, user_id, role, content, response_mode,
                sources_json, model_name, message_meta_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                message_id,
                thread_id,
                user_id,
                role,
                content,
                response_mode,
                json_dumps(sources) if sources is not None else None,
                model_name,
                json_dumps(message_meta) if message_meta is not None else None,
                utcnow(),
            ),
        )
    finally:
        cursor.close()
    return _fetch_messages(conn, thread_id, limit=1, before=None)[-1]



def _load_thread_state_envelope(raw: Any) -> ThreadStateEnvelope:
    try:
        return load_thread_state(raw, empty_retrieval_cache=create_empty_cache())
    except ConversationStateError as exc:
        print(
            json_dumps({
                "event": "conversation_state_load_failed",
                "reason": exc.__class__.__name__,
            }),
            file=sys.stderr,
            flush=True,
        )
        return ThreadStateEnvelope(retrieval_cache=create_empty_cache())


CONVERSATION_CONFLICT_MESSAGE = (
    "This conversation changed while your message was being processed. Please try again."
)


class ConversationConflictError(RuntimeError):
    """Raised when generated output is stale before persistence begins."""


def _lock_thread_for_persistence(conn, user_id: str, thread_id: str) -> Dict[str, Any]:
    """Lock one owned thread before inserting any child message rows."""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM conversation_threads "
            "WHERE id = %s AND user_id = %s AND deleted_at IS NULL FOR UPDATE",
            (thread_id, user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise LookupError("conversation thread no longer exists")
        return row
    finally:
        cursor.close()


def _merge_locked_thread_state(
    conn,
    *,
    locked_thread: Dict[str, Any],
    thread_id: str,
    user_id: str,
    expected_envelope: ThreadStateEnvelope,
    next_retrieval_cache: Dict[str, Any],
    transition: ProposedTransition,
    last_substantive_message_id: str,
    invalidate_retrieval_cache: bool = False,
) -> Dict[str, Any]:
    """Merge cache/state after the caller has locked the parent thread row."""
    cursor = conn.cursor(dictionary=True)
    try:
        current = _load_thread_state_envelope(locked_thread.get("thread_state_json"))
        current_state = current.conversation_state
        if current_state.revision != transition.expected_revision:
            print(
                json_dumps({
                    "event": "conversation_state_version_conflict",
                    "thread_id": thread_id,
                    "expected_revision": transition.expected_revision,
                    "current_revision": current_state.revision,
                    "transition": transition.action.value,
                }),
                file=sys.stderr,
                flush=True,
            )
            raise ConversationConflictError(CONVERSATION_CONFLICT_MESSAGE)

        merged = merge_for_commit(
            current,
            expected_envelope,
            next_retrieval_cache=next_retrieval_cache,
            transition=transition,
            last_substantive_message_id=last_substantive_message_id,
            force_cache_update=invalidate_retrieval_cache,
        )
        if not merged.cache_update_applied:
            print(
                json_dumps({
                    "event": "retrieval_cache_version_conflict",
                    "thread_id": thread_id,
                    "transition": transition.action.value,
                }),
                file=sys.stderr,
                flush=True,
            )
        cursor.execute(
            "UPDATE conversation_threads SET thread_state_json = %s, last_message_at = %s "
            "WHERE id = %s AND user_id = %s",
            (
                serialize_thread_state(merged.envelope),
                utcnow(),
                thread_id,
                user_id,
            ),
        )
        return {
            "transition_applied": merged.transition_applied,
            "cache_update_applied": merged.cache_update_applied,
            "state": merged.envelope.conversation_state,
            "version_conflict": False,
        }
    finally:
        cursor.close()


def _persist_conversation_turn(
    *,
    thread_id: str,
    user_id: str,
    session_id: str,
    expected_envelope: ThreadStateEnvelope,
    next_retrieval_cache: Dict[str, Any],
    transition: ProposedTransition,
    invalidate_retrieval_cache: bool = False,
    user_content: str,
    user_meta: Dict[str, Any],
    assistant_content: str,
    assistant_mode: str,
    assistant_sources: List[Dict[str, Any]],
    assistant_model_name: Optional[str],
    resolved_entity: Optional[Dict[str, str]],
    set_initial_title: bool,
    interaction_log_enabled: bool = True,
    expected_last_message_at: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Persist one complete turn using a single lock-first transaction."""
    conn = None
    refreshed_thread = None
    user_message_row = None
    assistant_message_row = None
    try:
        conn = get_db_connection()
        conn.start_transaction()
        locked_thread = _lock_thread_for_persistence(conn, user_id, thread_id)
        if (
            expected_last_message_at is not None
            and locked_thread.get("last_message_at") != expected_last_message_at
        ):
            raise ConversationConflictError(CONVERSATION_CONFLICT_MESSAGE)
        current = _load_thread_state_envelope(locked_thread.get("thread_state_json"))
        if current.conversation_state.revision != transition.expected_revision:
            print(
                json_dumps({
                    "event": "conversation_state_version_conflict",
                    "thread_id": thread_id,
                    "expected_revision": transition.expected_revision,
                    "current_revision": current.conversation_state.revision,
                    "transition": transition.action.value,
                }),
                file=sys.stderr,
                flush=True,
            )
            raise ConversationConflictError(CONVERSATION_CONFLICT_MESSAGE)

        user_message_row = _insert_message(
            conn,
            thread_id=thread_id,
            user_id=user_id,
            role="user",
            content=user_content,
            message_meta=user_meta,
        )
        assistant_meta = _assistant_message_metadata(resolved_entity=resolved_entity)
        assistant_message_row = _insert_message(
            conn,
            thread_id=thread_id,
            user_id=user_id,
            role="assistant",
            content=assistant_content,
            response_mode=assistant_mode,
            sources=assistant_sources,
            model_name=assistant_model_name,
            message_meta=assistant_meta,
        )
        if set_initial_title and locked_thread.get("title") == "New conversation":
            _update_thread(conn, thread_id, title=summarize_thread_title(user_content))

        transition_result = _merge_locked_thread_state(
            conn,
            locked_thread=locked_thread,
            thread_id=thread_id,
            user_id=user_id,
            expected_envelope=expected_envelope,
            next_retrieval_cache=next_retrieval_cache,
            transition=transition,
            last_substantive_message_id=assistant_message_row["id"],
            invalidate_retrieval_cache=invalidate_retrieval_cache,
        )
        refreshed_thread = _fetch_thread(conn, user_id, thread_id)
        conn.commit()
    except Exception:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn:
            conn.close()

    if not interaction_log_enabled:
        return {
            "thread": refreshed_thread,
            "user_message": user_message_row,
            "assistant_message": assistant_message_row,
            "transition": transition_result,
        }

    # Interaction logging is intentionally best-effort and starts only after the
    # resident turn has committed and released its pooled connection.
    log_conn = None
    log_id = None
    try:
        log_conn = get_db_connection()
        log_conn.start_transaction()
        log_id = log_interaction(
            session_id=session_id,
            client_query=user_content,
            app_response=assistant_content,
            mode=assistant_mode,
            user_id=user_id,
            thread_id=thread_id,
            message_id=assistant_message_row["id"],
            conn=log_conn,
        )
        if log_id is None:
            log_conn.rollback()
        else:
            assistant_meta = _assistant_message_metadata(
                log_id=log_id,
                resolved_entity=resolved_entity,
            )
            _update_message_meta(log_conn, assistant_message_row["id"], assistant_meta)
            log_conn.commit()
            assistant_message_row["message_meta_json"] = json_dumps(assistant_meta)
    except Exception as exc:
        if log_conn:
            try:
                log_conn.rollback()
            except Exception:
                pass
        _log_exception("Failed to log persisted conversation turn", exc)
    finally:
        if log_conn:
            log_conn.close()

    return {
        "thread": refreshed_thread,
        "user_message": user_message_row,
        "assistant_message": assistant_message_row,
        "transition": transition_result,
    }



def _update_message_meta(conn, message_id: str, message_meta: Dict[str, Any]) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE conversation_messages SET message_meta_json = %s WHERE id = %s",
            (json_dumps(message_meta), message_id),
        )
    finally:
        cursor.close()



def _canonicalize_source_label(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    lowered = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    lowered = re.sub(r"\s+", " ", lowered)
    if lowered in {"dorchester reporter", "dot reporter"} or "dotnews" in lowered:
        return ("dorchester reporter", "Dorchester Reporter")
    if lowered in {"admin knowledge", "community note", "community notes"}:
        return ("community notes", "Community Notes")
    if lowered in {"boston gov", "boston.gov", "bostongov"} or (
        "boston" in lowered and "gov" in lowered
    ):
        return ("boston.gov", "Boston.gov")
    return (lowered or value.lower() or "unknown", value or "Unknown")


def _friendly_policy_label(source_label: str) -> str:
    label = (source_label or "").strip()
    if label.lower().endswith(".txt"):
        return label[:-4]
    return label or "Policy document"


def _policy_public_url(source_label: str) -> str:
    base_dir = DOC_TYPE_DIRS["policy"]
    name = (source_label or "").strip()
    if name.lower().endswith(".txt"):
        name = name[:-4]
    if not name.lower().endswith(".html"):
        name = f"{name.replace(' ', '-')}.html"
    return urljoin(base_dir, name)


def _build_rag_source_path(meta: Dict[str, Any], source_label: str) -> Optional[str]:
    doc_type = meta.get("doc_type", "unknown")
    link = (meta.get("link") or "").strip()

    if doc_type == "transcript":
        return None
    if doc_type == "boston_gov_answer":
        return link or _BOSTON_GOV_HOME
    if doc_type == "policy":
        return _policy_public_url(source_label)
    if link.startswith(("http://", "https://")):
        return link

    base_dir = DOC_TYPE_DIRS.get(doc_type, "Data")
    if isinstance(base_dir, str) and base_dir.startswith(("http://", "https://")):
        normalized_source = source_label.replace(" ", "-")
        if normalized_source.endswith(".txt"):
            normalized_source = normalized_source[:-4] + ".html"
        return urljoin(base_dir, normalized_source)
    return None


def _append_rag_sources_from_metadata(
    metadata: List[Dict[str, Any]],
    sources: List[Dict[str, str]],
    *,
    seen: set[str],
    limit: int = 5,
) -> None:
    boston_entries = [m for m in metadata if m.get("doc_type") == "boston_gov_answer"]
    other_entries = [m for m in metadata if m.get("doc_type") != "boston_gov_answer"]
    prioritized = boston_entries + other_entries

    has_transcripts = False
    for meta in prioritized:
        doc_type = meta.get("doc_type", "unknown")
        if doc_type == "transcript":
            has_transcripts = True

    added = 0
    for meta in prioritized:
        if added >= limit:
            break
        doc_type = meta.get("doc_type", "unknown")
        if doc_type == "transcript":
            continue

        source = meta.get("source", "Unknown")
        dedupe_key, source_label = _canonicalize_source_label(source)
        if doc_type == "policy":
            source_label = _friendly_policy_label(source_label)
            dedupe_key = re.sub(r"[^a-z0-9]+", " ", source_label.lower()).strip()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        path = _build_rag_source_path(meta, source)
        entry: Dict[str, str] = {
            "type": "rag",
            "source": source_label,
            "doc_type": doc_type,
        }
        if path:
            entry["path"] = path
        sources.append(entry)
        added += 1

    if has_transcripts and _COMMUNITY_TRANSCRIPTS_LABEL not in seen:
        seen.add(_COMMUNITY_TRANSCRIPTS_LABEL)
        sources.append({
            "type": "rag",
            "source": _COMMUNITY_TRANSCRIPTS_LABEL,
            "doc_type": "transcript",
        })


def extract_sources(mode: str, result: Dict[str, Any]) -> List[Dict[str, str]]:
    sources: List[Dict[str, str]] = []

    def _weekly_events_sql(sql_query: str) -> bool:
        return bool(re.search(r"FROM\s+`?weekly_events`?", sql_query or "", re.IGNORECASE))

    def _event_sources_from_result(payload: Dict[str, Any]) -> List[Dict[str, str]]:
        explicit = payload.get("event_sources")
        if isinstance(explicit, list) and explicit:
            return [s for s in explicit if isinstance(s, dict)]
        try:
            from sql_chat.prepared_sql import build_event_sources  # noqa: WPS433

            rows = (payload.get("result") or {}).get("rows") or []
            if rows and (
                payload.get("prepared_intent") == "event_by_name"
                or _weekly_events_sql(str(payload.get("sql") or ""))
            ):
                return build_event_sources(rows, str(payload.get("question") or ""))
        except Exception:
            pass
        return []

    if mode == "sql":
        sources.extend(_event_sources_from_result(result))
        if not sources:
            sql_query = result.get("sql", "")
            if sql_query:
                match = re.search(r"FROM\s+`?(\w+)`?", sql_query, re.IGNORECASE)
                if match:
                    sources.append({"type": "sql", "table": match.group(1)})

    elif mode == "rag":
        metadata = result.get("metadata", [])
        seen: set[str] = set()
        _append_rag_sources_from_metadata(metadata, sources, seen=seen, limit=5)

    elif mode == "hybrid":
        sql_part = result.get("sql", {}) if isinstance(result.get("sql"), dict) else result.get("sql")
        rag_part = result.get("rag", {}) if isinstance(result.get("rag"), dict) else {}
        if isinstance(sql_part, dict):
            sql_query = sql_part.get("sql", "")
            if sql_query:
                match = re.search(r"FROM\s+`?(\w+)`?", sql_query, re.IGNORECASE)
                if match:
                    sources.append({"type": "sql", "table": match.group(1)})
        rag_metadata = rag_part.get("metadata", []) if isinstance(rag_part, dict) else []
        seen = set()
        _append_rag_sources_from_metadata(rag_metadata, sources, seen=seen, limit=5)

    return sources



def log_interaction(
    *,
    session_id: str,
    client_query: str,
    app_response: str,
    mode: str = "",
    log_id: Optional[int] = None,
    rating: str = "",
    flag_reason: str = "",
    flag_details: str = "",
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
    conn=None,
) -> Optional[int]:
    owns_connection = conn is None
    cursor = None
    try:
        if owns_connection:
            conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        if log_id:
            cursor.execute(
                "SELECT user_id, session_id FROM interaction_log WHERE id = %s",
                (log_id,),
            )
            existing = cursor.fetchone()
            if not existing:
                return None
            owned = False
            if user_id and existing.get("user_id") == user_id:
                owned = True
            elif session_id and existing.get("session_id") == session_id:
                if existing.get("user_id") is None:
                    owned = True
                elif user_id and existing.get("user_id") == user_id:
                    owned = True
            if not owned:
                return "forbidden"

            update_fields = []
            values: List[Any] = []
            if rating:
                update_fields.append("client_response_rating = %s")
                values.append(rating)
            if flag_reason:
                update_fields.append("flagged = TRUE")
                update_fields.append("flag_reason = %s")
                update_fields.append("flag_details = %s")
                update_fields.append("flagged_at = NOW()")
                values.extend([flag_reason, flag_details])
            if app_response:
                update_fields.append("app_response = %s")
                values.append(app_response)
            if update_fields:
                values.append(log_id)
                cursor.execute(
                    f"UPDATE interaction_log SET {', '.join(update_fields)} WHERE id = %s",
                    tuple(values),
                )
            if owns_connection:
                conn.commit()
            return log_id

        cursor.execute(
            """
            INSERT INTO interaction_log (
                session_id, app_version, client_query, app_response,
                data_selected, user_id, thread_id, message_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                Config.API_VERSION,
                client_query,
                app_response,
                mode,
                user_id,
                thread_id,
                message_id,
            ),
        )
        if owns_connection:
            conn.commit()
        return cursor.lastrowid
    except Exception as exc:
        if owns_connection and conn:
            try:
                conn.rollback()
            except Exception:
                pass
        _log_exception("Error logging interaction", exc)
        return None
    finally:
        if cursor:
            cursor.close()
        if owns_connection and conn:
            conn.close()



def _conversation_history_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    history: List[Dict[str, str]] = []
    for row in rows:
        if row["role"] not in {"user", "assistant"}:
            continue
        history.append({"role": row["role"], "content": row["content"]})
    return history[-20:]



def _execute_agent_response(
    message: str,
    *,
    conversation_history: List[Dict[str, str]],
    retrieval_cache: Optional[Dict[str, Any]],
    thread_id: Optional[str] = None,
    streaming: bool = False,
) -> Dict[str, Any]:
    reset_request_metrics()
    cache = retrieval_cache or create_empty_cache()
    emergency_decision, detector_latency_ms = _detect_emergency(message)
    if emergency_decision.is_active_emergency:
        return _emergency_agent_result(
            emergency_decision,
            cache,
            detector_latency_ms=detector_latency_ms,
        )
    has_history = bool(conversation_history)
    has_cache = bool(cache and cache.get("mode"))

    if is_small_talk(message):
        result = build_small_talk_result(message, cache, conversation_history)
        _log_agent_metrics(
            mode=result.get("mode", "chitchat"),
            cache_hit=False,
            thread_id=thread_id,
            streaming=streaming,
        )
        return result

    advisory = _try_311_advisory_response(message, conversation_history, cache)
    if advisory is not None:
        _log_agent_metrics(
            mode="advisory",
            cache_hit=False,
            thread_id=thread_id,
            streaming=streaming,
        )
        return advisory

    crime_clarify = _try_crime_clarification_response(
        message, conversation_history, cache,
    )
    if crime_clarify is not None:
        _log_agent_metrics(
            mode="advisory",
            cache_hit=False,
            thread_id=thread_id,
            streaming=streaming,
        )
        return crime_clarify

    # Semantic response cache: short-circuit near-duplicate first-turn questions.
    semantic_eligible = _semantic_cache_eligible(message, conversation_history)
    if semantic_eligible:
        cached = _semantic_cache.get(message)
        if cached and _is_cacheable_semantic_answer(cached.get("answer", "")):
            print(f"  ⚡ Semantic cache hit (similarity={cached.get('_similarity')})")
            out = {
                "answer": cached["answer"],
                "mode": cached.get("mode", "semantic_cache"),
                "sources": cached.get("sources", []),
                "result": cached.get("agent_result", {"answer": cached["answer"]}),
                "retrieval_cache": cache,
                "execution_degraded": bool(cached.get("execution_degraded")),
            }
            _log_agent_metrics(
                mode=out["mode"],
                cache_hit=True,
                thread_id=thread_id,
                streaming=streaming,
            )
            return out

    if has_history or has_cache:
        effective_message, force_fresh = resolve_effective_question(
            message, conversation_history, cache,
        )
        if force_fresh:
            history_check = {"needs_new_data": True, "reason": "rewritten follow-up"}
        else:
            history_check = _check_if_needs_new_data(
                effective_message, conversation_history, cache,
            )
    else:
        effective_message, force_fresh = message, False
        history_check = {"needs_new_data": True, "reason": "No history or cache"}

    if not history_check.get("needs_new_data", True) and (has_history or has_cache):
        answer = _answer_from_history(message, conversation_history, cache)
        out = {
            "answer": answer,
            "mode": "history",
            "sources": [],
            "result": {"answer": answer},
            "retrieval_cache": cache,
        }
        _log_agent_metrics(
            mode="history",
            cache_hit=False,
            thread_id=thread_id,
            streaming=streaming,
        )
        return out

    plan = _route_question(effective_message)
    mode = plan.get("mode", "hybrid")

    pipeline_out = run_execution_pipeline(
        effective_message, conversation_history, routing_plan=plan,
    )
    if pipeline_out is not None:
        mode, result, pipeline_metrics = pipeline_out
        answer = result.get("answer", "I couldn't find an answer to your question.")
        next_cache = build_retrieval_cache_from_result(mode, message, result)
        sources = extract_sources(mode, result)
        if (
            semantic_eligible
            and not pipeline_metrics.get("execution_degraded")
            and _is_cacheable_semantic_answer(answer)
        ):
            _semantic_cache.put(
                message,
                {
                    "answer": answer,
                    "mode": mode,
                    "sources": sources,
                    "agent_result": result,
                    "execution_degraded": False,
                },
            )
        _log_agent_metrics(
            mode=mode,
            cache_hit=False,
            thread_id=thread_id,
            streaming=streaming,
            extra={
                k: v
                for k, v in {
                    **pipeline_metrics,
                    "prepared_intent": result.get("prepared_intent"),
                }.items()
                if v is not None and v != ""
            } or None,
        )
        return {
            "answer": answer,
            "mode": mode,
            "sources": sources,
            "result": result,
            "retrieval_cache": next_cache,
            "execution_degraded": bool(pipeline_metrics.get("execution_degraded")),
        }

    if mode == "sql":
        result = _run_sql(effective_message, conversation_history)
        next_cache = build_retrieval_cache(
            mode="sql",
            question=message,
            answer=result.get("answer", ""),
            sql_result=result.get("result"),
            sql_query=result.get("sql"),
        )
    elif mode == "rag":
        result = _run_rag(effective_message, plan, conversation_history)
        next_cache = build_retrieval_cache(
            mode="rag",
            question=message,
            answer=result.get("answer", ""),
            rag_chunks=result.get("chunks"),
            rag_metadata=result.get("metadata"),
        )
    else:
        result = _run_hybrid(effective_message, plan, conversation_history)
        sql_part = result.get("sql", {}) if isinstance(result.get("sql"), dict) else {}
        rag_part = result.get("rag", {}) if isinstance(result.get("rag"), dict) else {}
        next_cache = build_retrieval_cache(
            mode="hybrid",
            question=message,
            answer=result.get("answer", ""),
            sql_result=sql_part.get("result"),
            sql_query=sql_part.get("sql"),
            rag_chunks=rag_part.get("chunks"),
            rag_metadata=rag_part.get("metadata"),
        )

    answer = result.get("answer", "I couldn't find an answer to your question.")
    sources = extract_sources(mode, result)

    # Store fresh first-turn answers for future near-duplicate questions.
    if semantic_eligible and _is_cacheable_semantic_answer(answer):
        _semantic_cache.put(
            message,
            {
                "answer": answer,
                "mode": mode,
                "sources": sources,
                "agent_result": result,
            },
        )

    _log_agent_metrics(
        mode=mode,
        cache_hit=False,
        thread_id=thread_id,
        streaming=streaming,
        extra={
            k: v
            for k, v in {
                "prepared_intent": result.get("prepared_intent"),
                "intent": plan.get("intent"),
                "router": plan.get("router"),
                "period_days": (plan.get("query_plan") or {}).get("period_days"),
                "period_label": (plan.get("query_plan") or {}).get("period_label"),
                "metric": (plan.get("query_plan") or {}).get("metric"),
            }.items()
            if v is not None and v != ""
        } or None,
    )
    return {
        "answer": answer,
        "mode": mode,
        "sources": sources,
        "result": result,
        "retrieval_cache": next_cache,
    }


def _validate_optional_event_context(
    payload: Dict[str, Any],
) -> Optional[ValidatedEventContext]:
    if "ui_context" not in payload:
        return None
    return validate_event_ui_context(payload["ui_context"])


def _resolve_validated_event_context(
    conn,
    context: Optional[ValidatedEventContext],
) -> Optional[EventResolution]:
    if context is None:
        return None
    resolution = resolve_event_context(conn, context)
    if resolution.diagnostic_mismatches:
        fields = ",".join(resolution.diagnostic_mismatches)
        print(
            "UI event context differs from the authoritative row: "
            f"event_id={context.entity_id}, fields={fields}",
            file=sys.stderr,
        )
    return resolution


def _selected_event_agent_result(
    message: str,
    resolution: EventResolution,
    retrieval_cache: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    selected = build_selected_event_response(message, resolution)
    return {
        "answer": selected.answer,
        "mode": selected.mode,
        "sources": selected.sources,
        "result": selected.result,
        "retrieval_cache": retrieval_cache or create_empty_cache(),
        "resolved_entity": selected.resolved_entity,
        "model_name": None,
    }


def _active_event_agent_result(
    message: str,
    resolution: EventResolution,
    property_name: EventProperty,
    retrieval_cache: Dict[str, Any],
) -> Dict[str, Any]:
    selected = build_active_event_response(
        message,
        resolution,
        property_name=property_name.value,
    )
    return {
        "answer": selected.answer,
        "mode": selected.mode,
        "sources": selected.sources,
        "result": selected.result,
        "retrieval_cache": retrieval_cache,
        "resolved_entity": selected.resolved_entity if resolution.event else None,
        "model_name": None,
    }


def _missing_event_reference_agent_result(retrieval_cache: Dict[str, Any]) -> Dict[str, Any]:
    answer = MISSING_EVENT_REFERENCE_MESSAGE
    return {
        "answer": answer,
        "mode": "clarification",
        "sources": [],
        "result": {"answer": answer},
        "retrieval_cache": retrieval_cache,
        "model_name": None,
    }


def _detect_emergency(message: str) -> tuple[EmergencyDecision, float]:
    started = time.perf_counter()
    decision = detect_active_emergency(message)
    elapsed = (time.perf_counter() - started) * 1000.0
    return decision, elapsed


def _emergency_agent_result(
    decision: EmergencyDecision,
    retrieval_cache: Optional[Dict[str, Any]],
    *,
    detector_latency_ms: float = 0.0,
) -> Dict[str, Any]:
    if not decision.is_active_emergency:
        raise ValueError("emergency response requires an active emergency decision")
    diagnostic = decision.to_dict()
    return {
        "answer": EMERGENCY_RESPONSE,
        "mode": "emergency",
        "sources": [],
        "result": {
            "answer": EMERGENCY_RESPONSE,
            "emergency_decision": diagnostic,
        },
        "retrieval_cache": copy.deepcopy(retrieval_cache or create_empty_cache()),
        "model_name": None,
        "llm_calls": 0,
        "embedding_calls": 0,
        "sql_calls": 0,
        "vector_calls": 0,
        "detector_latency_ms": round(max(0.0, detector_latency_ms), 4),
        "emergency_decision": diagnostic,
    }


def _emergency_conversation_transition(
    state: ConversationState,
) -> ConversationTransition:
    retained = []
    if state.active_topic is not None:
        retained.append(StateField.ACTIVE_TOPIC)
    if state.active_entity is not None:
        retained.append(StateField.ACTIVE_ENTITY)
    if state.location is not None:
        retained.append(StateField.LOCATION)
    if state.time_range is not None:
        retained.append(StateField.TIME_RANGE)
    if any(value != "ANY" for value in state.filters.to_dict().values()):
        retained.append(StateField.FILTERS)
    if state.previous_route is not None:
        retained.append(StateField.PREVIOUS_ROUTE)
    return ConversationTransition(
        action=TransitionAction.NO_CHANGE,
        relationship=ConversationRelationship.STANDALONE,
        retained=tuple(retained),
        replaced=(),
        cleared=(),
        next_state=state,
        cache_action=CacheAction.PRESERVE,
        reason_codes=(TransitionReason.EMERGENCY_CONTEXT_PRESERVED,),
        commit_allowed=True,
        requires_clarification=False,
        completion_status=CompletionStatus.ACCEPTABLE,
    )


def _log_emergency_metrics(
    decision: EmergencyDecision,
    *,
    thread_id: Optional[str],
    streaming: bool,
    success: bool,
    detector_latency_ms: float,
    failure_code: Optional[str] = None,
) -> None:
    extra = {
        "emergency_category": decision.category.value,
        "emergency_reason_code": decision.reason_code.value,
        "decision_source": decision.decision_source.value,
        "success": bool(success),
        "detector_latency_ms": round(max(0.0, detector_latency_ms), 4),
        "embedding_calls": 0,
        "sql_calls": 0,
        "vector_calls": 0,
    }
    if failure_code:
        extra["failure_code"] = failure_code
    _log_agent_metrics(
        mode="emergency",
        cache_hit=False,
        thread_id=thread_id,
        streaming=streaming,
        extra=extra,
    )


def _resolve_active_event_request(
    conn,
    message: str,
    *,
    state: ConversationState,
    relationship: Optional[RelationshipDecision],
    retrieval_cache: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], bool]:
    property_name = detect_event_property(message)
    if property_name is None or relationship is None:
        return None, False
    if relationship.reason_code is ReasonCode.MISSING_REFERENCE:
        return _missing_event_reference_agent_result(retrieval_cache), False
    if relationship.reason_code is not ReasonCode.ACTIVE_EVENT_PROPERTY_QUESTION:
        return None, False
    active_entity = state.active_entity
    if active_entity is None or active_entity.entity_type != "event":
        return _missing_event_reference_agent_result(retrieval_cache), False
    resolution = resolve_event_by_id(conn, active_entity.entity_id)
    return (
        _active_event_agent_result(
            message,
            resolution,
            property_name,
            retrieval_cache,
        ),
        not bool(resolution.event),
    )


def _typed_authoritative_event_id(agent_result: Dict[str, Any]) -> Optional[str]:
    if agent_result.get("mode") != "sql":
        return None
    result = agent_result.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("prepared_intent") != "event_by_name" or not result.get("db_backed"):
        return None
    sql_result = result.get("result")
    rows = sql_result.get("rows") if isinstance(sql_result, dict) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    try:
        event_id = int(rows[0].get("id"))
    except (TypeError, ValueError):
        return None
    return str(event_id) if event_id > 0 else None


def _authoritative_transition_entity(
    *,
    agent_result: Dict[str, Any],
    selected_resolution: Optional[EventResolution],
) -> Optional[ActiveEntity]:
    if selected_resolution is not None and selected_resolution.event and selected_resolution.status in {
        "exact_id", "fallback_title_date",
    }:
        return ActiveEntity.event(selected_resolution.event["id"])
    typed_event_id = _typed_authoritative_event_id(agent_result)
    return ActiveEntity.event(typed_event_id) if typed_event_id is not None else None


def _plan_conversation_transition(
    *,
    state: ConversationState,
    relationship: Optional[RelationshipDecision],
    dimensions: Optional[ConversationDimensions],
    message: str,
    mode: str,
    agent_result: Dict[str, Any],
    selected_resolution: Optional[EventResolution],
    stale_active_event: bool,
    event_query_plan: Optional[EventQueryPlan] = None,
    event_retrieval_matched: Optional[bool] = None,
    partial: bool = False,
) -> ConversationTransition:
    completion = classify_completion_status(agent_result, partial=partial)
    transition = plan_conversation_transition(
        current=state,
        relationship=relationship,
        dimensions=dimensions,
        authoritative_entity=_authoritative_transition_entity(
            agent_result=agent_result,
            selected_resolution=selected_resolution,
        ),
        detected_topic=detect_civic_topic(message),
        completed_route=mode,
        completion_status=completion,
        stale_active_entity=stale_active_event,
        reference_context_applied=(
            event_query_plan is not None
            and event_query_plan.status is EventPlanStatus.READY
            if dimensions
            and (
                dimensions.references.location_reference is not None
                or dimensions.references.time_reference is not None
            )
            else True
        ),
    )
    if event_query_plan is None:
        return transition
    field_by_dimension = {
        EventQueryDimension.TOPIC: StateField.ACTIVE_TOPIC,
        EventQueryDimension.TIME_RANGE: StateField.TIME_RANGE,
        EventQueryDimension.LOCATION: StateField.LOCATION,
        EventQueryDimension.FILTERS: StateField.FILTERS,
    }
    event_reasons = set(event_query_plan.reason_codes)
    requested_event_dimensions = [EventQueryDimension.TOPIC]
    if event_reasons & {
        EventPlanReason.EXPLICIT_TIME,
        EventPlanReason.TRUSTED_TIME_REFERENCE,
        EventPlanReason.MISSING_TIME_REFERENCE,
        EventPlanReason.AMBIGUOUS_TIME,
    }:
        requested_event_dimensions.append(EventQueryDimension.TIME_RANGE)
    if event_reasons & {
        EventPlanReason.EXPLICIT_LOCATION,
        EventPlanReason.TRUSTED_LOCATION_REFERENCE,
        EventPlanReason.MISSING_LOCATION_REFERENCE,
        EventPlanReason.AMBIGUOUS_LOCATION,
    }:
        requested_event_dimensions.append(EventQueryDimension.LOCATION)
    if EventQueryDimension.FILTERS in event_query_plan.unapplied_dimensions:
        requested_event_dimensions.append(EventQueryDimension.FILTERS)
    requested = tuple(field_by_dimension[item] for item in requested_event_dimensions)
    return with_transition_diagnostics(
        transition,
        requested_dimensions=tuple(dict.fromkeys(requested)),
        applied_dimensions=tuple(
            field_by_dimension[item] for item in event_query_plan.applied_dimensions
        ),
        unapplied_dimensions=tuple(
            field_by_dimension[item] for item in event_query_plan.unapplied_dimensions
        ),
        retrieval_matched=event_retrieval_matched,
    )


def _cache_for_conversation_transition(
    *,
    current_cache: Dict[str, Any],
    next_cache: Dict[str, Any],
    transition: ConversationTransition,
    replacement_cache_valid: bool = False,
) -> tuple[Dict[str, Any], bool]:
    if transition.commit_allowed and transition.cache_action is CacheAction.INVALIDATE:
        if replacement_cache_valid:
            return next_cache, True
        return create_empty_cache(), True
    if (
        not transition.commit_allowed
        or transition.completion_status is not CompletionStatus.ACCEPTABLE
    ):
        return copy.deepcopy(current_cache), False
    return next_cache, False


def _state_aware_event_candidate(
    message: str,
    *,
    state: ConversationState,
    relationship: Optional[RelationshipDecision],
    dimensions: Optional[ConversationDimensions],
) -> bool:
    if _explicit_event_list_request(message):
        return True
    if (
        state.active_topic is not CanonicalTopic.EVENTS
        or relationship is None
        or dimensions is None
        or relationship.relationship
        not in {ConversationRelationship.REFINEMENT, ConversationRelationship.FOLLOW_UP}
    ):
        return False
    reason_values = {reason.value for reason in dimensions.reason_codes}
    return bool(
        dimensions.location is not None
        or dimensions.time_range is not None
        or dimensions.references.location_reference is not None
        or dimensions.references.time_reference is not None
        or any(
            value.startswith((
                "explicit_free_",
                "explicit_paid_",
                "explicit_morning_",
                "explicit_afternoon_",
                "explicit_evening_",
                "explicit_virtual_",
                "explicit_in_person_",
                "explicit_hybrid_",
                "explicit_registration_",
            ))
            or value == "unsupported_filter"
            for value in reason_values
        )
    )


def _explicit_event_list_request(message: str) -> bool:
    return is_generic_events_list_question(message) or is_direct_event_list_request(message)


def _state_aware_event_agent_result(
    conn,
    *,
    message: str,
    state: ConversationState,
    relationship: Optional[RelationshipDecision],
    dimensions: Optional[ConversationDimensions],
    retrieval_cache: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], Optional[EventQueryPlan]]:
    candidate = _state_aware_event_candidate(
        message,
        state=state,
        relationship=relationship,
        dimensions=dimensions,
    )
    if not candidate or dimensions is None:
        return None, None
    plan = plan_event_query(
        relationship=relationship,
        dimensions=dimensions,
        state=state,
        event_list_requested=True,
    )
    if plan is None:
        return None, None
    if plan.status is not EventPlanStatus.READY:
        answer = compose_event_answer(plan, [])
        return {
            "answer": answer,
            "mode": "structured_events",
            "sources": [],
            "result": {
                "answer": answer,
                "event_query_context": plan.to_dict(),
                "event_retrieval_matched": False,
            },
            "retrieval_cache": copy.deepcopy(retrieval_cache),
            "model_name": None,
            "llm_calls": 0,
            "execution_degraded": True,
            "event_cache_replacement_valid": False,
        }, plan

    if event_cache_matches(retrieval_cache, plan):
        rows, answer, sources = cached_event_result(retrieval_cache)
        if answer:
            return {
                "answer": answer,
                "mode": "structured_events",
                "sources": sources,
                "result": {
                    "answer": answer,
                    "rows": rows,
                    "event_query_context": plan.to_dict(),
                    "event_retrieval_matched": True,
                    "cache_hit": True,
                    "authoritative_no_results": not rows,
                },
                "retrieval_cache": copy.deepcopy(retrieval_cache),
                "model_name": None,
                "llm_calls": 0,
                "event_cache_replacement_valid": False,
            }, plan

    try:
        rows, candidate_count = retrieve_events(conn, plan)
        answer = compose_event_answer(plan, rows, candidate_count=candidate_count)
        sources = build_structured_event_sources(rows)
        next_cache = build_event_cache(
            base_cache=create_empty_cache(),
            plan=plan,
            rows=rows,
            answer=answer,
            sources=sources,
        )
        return {
            "answer": answer,
            "mode": "structured_events",
            "sources": sources,
            "result": {
                "answer": answer,
                "rows": rows,
                "event_query_context": plan.to_dict(),
                "event_retrieval_matched": True,
                "candidate_count": candidate_count,
                "authoritative_no_results": not rows,
            },
            "retrieval_cache": next_cache,
            "model_name": None,
            "llm_calls": 0,
            "event_cache_replacement_valid": True,
        }, plan
    except Exception as exc:  # noqa: BLE001
        _log_exception("Deterministic event retrieval failed", exc)
        answer = (
            "I couldn't load the current event listings right now. Your previous event "
            "search context has been preserved."
        )
        return {
            "answer": answer,
            "mode": "structured_events",
            "sources": [],
            "result": {
                "answer": answer,
                "event_query_context": plan.to_dict(),
                "event_retrieval_matched": False,
                "error": "event_retrieval_unavailable",
            },
            "retrieval_cache": copy.deepcopy(retrieval_cache),
            "model_name": None,
            "llm_calls": 0,
            "execution_degraded": True,
            "event_cache_replacement_valid": False,
        }, plan


def _execute_agent_response_with_event_context(
    message: str,
    *,
    conversation_history: List[Dict[str, str]],
    retrieval_cache: Optional[Dict[str, Any]],
    event_resolution: Optional[EventResolution],
    thread_id: Optional[str] = None,
    streaming: bool = False,
    emergency_decision: Optional[EmergencyDecision] = None,
) -> Dict[str, Any]:
    decision = emergency_decision
    detector_latency_ms = 0.0
    if decision is None:
        decision, detector_latency_ms = _detect_emergency(message)
    if decision.is_active_emergency:
        reset_request_metrics()
        return _emergency_agent_result(
            decision,
            retrieval_cache,
            detector_latency_ms=detector_latency_ms,
        )
    if event_resolution is None:
        return _execute_agent_response(
            message,
            conversation_history=conversation_history,
            retrieval_cache=retrieval_cache,
            thread_id=thread_id,
            streaming=streaming,
        )
    reset_request_metrics()
    result = _selected_event_agent_result(message, event_resolution, retrieval_cache)
    _log_agent_metrics(
        mode=result["mode"],
        thread_id=thread_id,
        streaming=streaming,
        extra={"ui_event_resolution": event_resolution.status},
    )
    return result


def _assistant_message_metadata(
    *,
    log_id: Optional[int] = None,
    resolved_entity: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"log_id": log_id}
    if resolved_entity:
        metadata["resolved_entity"] = resolved_entity
    return metadata


def _relationship_llm_classifier(prompt: str, timeout_seconds: float) -> str:
    """One bounded structured classifier call used only when explicitly enabled."""
    client = _get_llm_client()
    model_name = os.getenv(
        "GEMINI_FAST_MODEL",
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
    )
    model = client.GenerativeModel(model_name)
    increment_llm_call_counter()
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0, "response_mime_type": "application/json"},
        request_options={"timeout": max(0.25, float(timeout_seconds))},
    )
    return str(getattr(response, "text", "") or "")


def _dimensions_llm_classifier(prompt: str, timeout_seconds: float) -> str:
    """Optional bounded classifier; its calls do not affect answer model metrics."""
    client = _get_llm_client()
    model_name = os.getenv(
        "GEMINI_FAST_MODEL",
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
    )
    model = client.GenerativeModel(model_name)
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0, "response_mime_type": "application/json"},
        request_options={"timeout": max(0.25, float(timeout_seconds))},
    )
    return str(getattr(response, "text", "") or "")


def _classify_relationship_shadow(
    message: str,
    history_rows: List[Dict[str, Any]],
    *,
    thread_id: str,
    authoritative_active_event: bool = False,
) -> Optional[RelationshipDecision]:
    if not Config.CONVERSATION_RELATIONSHIP_SHADOW_ENABLED:
        return None
    try:
        decision = classify_shadow_relationship(
            message,
            history_rows,
            enabled=True,
            llm_fallback_enabled=Config.CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED,
            llm_classifier=(
                _relationship_llm_classifier
                if Config.CONVERSATION_RELATIONSHIP_LLM_FALLBACK_ENABLED
                else None
            ),
            authoritative_active_event=authoritative_active_event,
        )
    except Exception as exc:  # noqa: BLE001
        _log_exception("Conversation relationship shadow classification failed", exc)
        decision = conservative_fallback(ReasonCode.CLASSIFIER_FAILURE)
    diagnostic = decision.to_dict()
    log_payload = {
        "event": "conversation_relationship_shadow",
        "thread_id": thread_id,
        "relationship": diagnostic["relationship"],
        "decision_source": diagnostic["decision_source"],
        "reason_code": diagnostic["reason_code"],
    }
    request_id = get_request_id()
    if request_id:
        log_payload["request_id"] = request_id
    print(json_dumps(log_payload), file=sys.stderr, flush=True)
    return decision


def _dimension_context_from_state(state: ConversationState) -> DimensionContext:
    active_event = state.active_entity is not None
    return DimensionContext(
        active_topic=state.active_topic,
        location=state.location,
        time_range=state.time_range,
        authoritative_active_event=active_event,
    )


def _extract_dimensions_shadow(
    message: str,
    *,
    thread_id: str,
    conversation_state: ConversationState,
    event_list_context: bool = False,
) -> Optional[ConversationDimensions]:
    if not Config.CONVERSATION_DIMENSIONS_SHADOW_ENABLED:
        return None
    try:
        result = extract_conversation_dimensions(
            message,
            enabled=True,
            context=_dimension_context_from_state(conversation_state),
            llm_fallback_enabled=Config.CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED,
            llm_classifier=(
                _dimensions_llm_classifier
                if Config.CONVERSATION_DIMENSIONS_LLM_FALLBACK_ENABLED
                else None
            ),
            resolve_yearless_named_dates=event_list_context,
        )
    except Exception as exc:  # noqa: BLE001
        _log_exception("Conversation dimension shadow extraction failed", exc)
        return None
    if result is None:
        return None
    log_payload = {
        "event": "conversation_dimensions_shadow",
        "thread_id": thread_id,
        "worker_pid": os.getpid(),
        **dimension_diagnostic_metrics(result),
    }
    request_id = get_request_id()
    if request_id:
        log_payload["request_id"] = request_id
    print(json_dumps(log_payload), file=sys.stderr, flush=True)
    return result


def _user_message_metadata(
    *,
    event_context: Optional[ValidatedEventContext],
    relationship: Optional[RelationshipDecision],
    dimensions: Optional[ConversationDimensions],
    transition: Optional[ConversationTransition] = None,
    active_event_behavior_applied: bool = False,
    emergency_decision: Optional[EmergencyDecision] = None,
    event_query_plan: Optional[EventQueryPlan] = None,
) -> Optional[Dict[str, Any]]:
    metadata: Dict[str, Any] = {}
    if event_context is not None:
        metadata.update(event_context.user_message_metadata())
    if relationship is not None:
        metadata["conversation_relationship"] = relationship.to_dict()
    if dimensions is not None:
        metadata["conversation_dimensions_shadow"] = dimensions.to_dict()
    if transition is not None:
        metadata["conversation_transition"] = transition.to_diagnostic_dict()
    if event_query_plan is not None:
        metadata["event_query_context"] = event_query_plan.to_dict()
    if active_event_behavior_applied:
        metadata["active_event_followup_applied"] = True
    if emergency_decision is not None and emergency_decision.is_active_emergency:
        metadata["emergency_decision"] = emergency_decision.to_dict()
    return metadata or None


# =============================================================================
# Request lifecycle
# =============================================================================
@app.before_request
def before_request_handler():
    if request.method == "OPTIONS":
        return ("", 204)

    g.api_key_authenticated = False
    g.current_user_row = None
    g.current_user = None
    g.linked_providers = []
    g.session_row = None
    g.session_id = None
    g.bearer_session = False
    g.is_guest = False
    g.clear_auth_cookies = False
    g.csrf_cookie_written = False
    g.request_id = str(uuid.uuid4())[:12]

    provided_api_key = request.headers.get("RethinkAI-API-Key", "")
    if provided_api_key and provided_api_key in Config.RETHINKAI_API_KEYS:
        g.api_key_authenticated = True
        if "session_id" not in session:
            session.permanent = True
            session["session_id"] = str(uuid.uuid4())
        g.session_id = session.get("session_id")

    session_token = request.cookies.get(Config.AUTH_SESSION_COOKIE_NAME, "")
    via_bearer = False
    if not session_token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            session_token = auth_header[7:].strip()
            via_bearer = bool(session_token)

    if not session_token:
        return None

    conn = None
    try:
        conn = get_db_connection()
        activated = _activate_web_session(conn, session_token, via_bearer=via_bearer)
        if not activated:
            if not via_bearer:
                g.clear_auth_cookies = True
    except Exception as exc:
        _log_exception("Warning: session lookup failed", exc)
    finally:
        if conn:
            conn.close()


@app.after_request
def after_request_handler(response):
    if getattr(g, "clear_auth_cookies", False):
        _clear_auth_cookies(response)
    if not request.cookies.get(Config.AUTH_CSRF_COOKIE_NAME) and not getattr(g, "csrf_cookie_written", False):
        response.set_cookie(
            Config.AUTH_CSRF_COOKIE_NAME,
            generate_token(24),
            **_cookie_kwargs(httponly=False),
        )
        g.csrf_cookie_written = True
    return response


# =============================================================================
# Auth endpoints
# =============================================================================
@app.route("/auth/me", methods=["GET"])
def auth_me():
    return jsonify(
        {
            "authenticated": bool(g.get("current_user")),
            "user": g.get("current_user"),
            "is_guest": bool(g.get("is_guest")),
        }
    )


@app.route("/auth/guest", methods=["POST"])
def auth_guest():
    """Create an ephemeral guest user + tab-scoped Bearer session (no cookies)."""
    ip_address = get_client_ip(request.headers, request.remote_addr)
    limited = _rate_limit_or_none(
        "guest_create",
        ip_address,
        Config.RATE_LIMIT_GUEST_CREATE_PER_IP,
        Config.RATE_LIMIT_GUEST_CREATE_WINDOW_SECONDS,
    )
    if limited:
        return limited

    conn = None
    try:
        conn = get_db_connection()
        user = _create_guest_user(conn)
        session_info = _create_web_session(conn, user["id"], is_guest=True)
        _record_auth_event(conn, "guest_bootstrap", success=True, user_id=user["id"])
        conn.commit()

        providers = _provider_names(conn, user["id"])
        fresh_user = _fetch_user_by_id(conn, user["id"])
        return jsonify(
            {
                "user": serialize_user(fresh_user, providers),
                "session_token": session_info["session_token"],
                "csrf_token": session_info["csrf_token"],
                "expires_at": session_info["expires_at"].isoformat(),
            }
        ), 201
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error creating guest session", exc)
        return _json_error("Failed to create guest session.", 500, "guest_bootstrap_failed")
    finally:
        if conn:
            conn.close()


@app.route("/auth/signup", methods=["POST"])
def auth_signup():
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    ip_address = get_client_ip(request.headers, request.remote_addr)
    limited = _rate_limit_or_none(
        "auth_signup",
        ip_address,
        Config.RATE_LIMIT_AUTH_PER_IP,
        Config.RATE_LIMIT_AUTH_WINDOW_SECONDS,
    )
    if limited:
        return limited

    payload = request.get_json() or {}
    email = normalize_email(payload.get("email", ""))
    username = normalize_username(payload.get("username", ""))
    password = payload.get("password", "")

    username_error = validate_username(username)
    if username_error:
        return _json_error(username_error, 400, "invalid_username")
    password_error = validate_password(password, username, email)
    if password_error:
        return _json_error(password_error, 400, "invalid_password")
    if not email or "@" not in email:
        return _json_error("A valid email address is required.", 400, "invalid_email")

    conn = None
    try:
        conn = get_db_connection()
        existing_user = _fetch_user_by_email(conn, email)
        if existing_user:
            return _json_error("An account with that email already exists.", 409, "email_exists")
        existing_username = _fetch_user_by_username(conn, username)
        if existing_username:
            return _json_error("That username is already taken.", 409, "username_exists")

        user = _create_user(conn, email, username, profile_complete=True)
        _promote_user_to_admin_if_configured(
            conn,
            user_id=user["id"],
            email=email,
            current_role=user.get("role"),
            audit_event="admin_role_bootstrap",
        )
        _create_auth_identity(
            conn,
            user_id=user["id"],
            provider="password",
            password_hash_value=hash_password(password),
        )
        _update_user_login_stamp(conn, user["id"])
        _update_identity_last_used(conn, user["id"], "password")
        session_info = _create_web_session(conn, user["id"])
        _record_auth_event(conn, "signup_password", success=True, user_id=user["id"], details={"email": email})
        conn.commit()

        providers = _provider_names(conn, user["id"])
        fresh_user = _fetch_user_by_id(conn, user["id"])
        response = jsonify({"user": serialize_user(fresh_user, providers)})
        _set_auth_cookies(response, session_info["session_token"], session_info["csrf_token"], session_info["expires_at"])
        return response, 201
    except RuntimeError as exc:
        if conn:
            conn.rollback()
        return _json_error(str(exc), 500, "dependency_missing")
    except mysql.connector.IntegrityError:
        if conn:
            conn.rollback()
        return _json_error("An account with that email or username already exists.", 409, "signup_conflict")
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error in signup", exc)
        return _json_error("Failed to create account.", 500, "signup_failed")
    finally:
        if conn:
            conn.close()


@app.route("/auth/login", methods=["POST"])
def auth_login():
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    email = normalize_email(payload.get("email", ""))
    password = payload.get("password", "")
    ip_address = get_client_ip(request.headers, request.remote_addr)

    if not email or not password:
        return _json_error("Email and password are required.", 400, "missing_credentials")

    limited = _rate_limit_or_none(
        "auth_login",
        ip_address,
        Config.RATE_LIMIT_AUTH_PER_IP,
        Config.RATE_LIMIT_AUTH_WINDOW_SECONDS,
    )
    if limited:
        return limited

    conn = None
    try:
        conn = get_db_connection()
        login_attempt = _fetch_login_attempt(conn, email, ip_address)
        if _is_locked(login_attempt):
            return _json_error("Too many failed login attempts. Try again later.", 429, "login_locked")

        login_row = _fetch_password_login_row(conn, email)
        if not login_row or login_row.get("status") != "active":
            locked_until = _record_failed_login(conn, email, ip_address)
            _record_auth_event(conn, "login_password", success=False, details={"email": email, "locked_until": locked_until.isoformat() if locked_until else None})
            conn.commit()
            return _json_error("Invalid email or password.", 401, "invalid_credentials")

        password_ok, replacement_hash = verify_password(login_row["password_hash"], password)
        if not password_ok:
            locked_until = _record_failed_login(conn, email, ip_address)
            _record_auth_event(conn, "login_password", success=False, user_id=login_row["id"], details={"email": email, "locked_until": locked_until.isoformat() if locked_until else None})
            conn.commit()
            return _json_error("Invalid email or password.", 401, "invalid_credentials")

        if replacement_hash:
            _update_password_hash(conn, login_row["identity_id"], replacement_hash)

        guest_session_token = (payload.get("guest_session_token") or "").strip()
        if guest_session_token:
            guest_session = _current_session_row(conn, guest_session_token)
            if guest_session and guest_session.get("is_guest"):
                _revoke_session(conn, guest_session["session_id"])

        _promote_user_to_admin_if_configured(
            conn,
            user_id=login_row["id"],
            email=email,
            current_role=login_row.get("role"),
            audit_event="admin_role_bootstrap",
        )
        _clear_login_attempts(conn, email, ip_address)
        _update_user_login_stamp(conn, login_row["id"])
        _update_identity_last_used(conn, login_row["id"], "password")
        session_info = _create_web_session(conn, login_row["id"])
        _record_auth_event(conn, "login_password", success=True, user_id=login_row["id"], details={"email": email})
        conn.commit()

        providers = _provider_names(conn, login_row["id"])
        user_row = _fetch_user_by_id(conn, login_row["id"])
        response = jsonify({"user": serialize_user(user_row, providers)})
        _set_auth_cookies(response, session_info["session_token"], session_info["csrf_token"], session_info["expires_at"])
        return response
    except RuntimeError as exc:
        if conn:
            conn.rollback()
        return _json_error(str(exc), 500, "dependency_missing")
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error in login", exc)
        return _json_error("Failed to log in.", 500, "login_failed")
    finally:
        if conn:
            conn.close()


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    result = _require_user(allow_incomplete=True)
    if result:
        return result
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    conn = None
    try:
        conn = get_db_connection()
        _revoke_session(conn, g.session_row["session_id"])
        _record_auth_event(conn, "logout", success=True, user_id=g.current_user_row["id"])
        conn.commit()
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error in logout", exc)
        return _json_error("Failed to log out.", 500, "logout_failed")
    finally:
        if conn:
            conn.close()

    response = jsonify({"ok": True})
    _clear_auth_cookies(response)
    return response


@app.route("/auth/complete-profile", methods=["POST"])
def auth_complete_profile():
    result = _require_user(allow_incomplete=True)
    if result:
        return result
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    username = normalize_username(payload.get("username", ""))
    username_error = validate_username(username)
    if username_error:
        return _json_error(username_error, 400, "invalid_username")

    conn = None
    try:
        conn = get_db_connection()
        existing = _fetch_user_by_username(conn, username)
        if existing and existing["id"] != g.current_user_row["id"]:
            return _json_error("That username is already taken.", 409, "username_exists")

        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE users SET username = %s, profile_complete = TRUE WHERE id = %s",
                (username, g.current_user_row["id"]),
            )
        finally:
            cursor.close()
        _record_auth_event(conn, "complete_profile", success=True, user_id=g.current_user_row["id"], details={"username": username})
        conn.commit()

        user_row = _fetch_user_by_id(conn, g.current_user_row["id"])
        providers = _provider_names(conn, g.current_user_row["id"])
        return jsonify({"user": serialize_user(user_row, providers)})
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error completing profile", exc)
        return _json_error("Failed to complete profile.", 500, "profile_update_failed")
    finally:
        if conn:
            conn.close()


# =============================================================================
# Conversation endpoints
# =============================================================================
@app.route("/conversations", methods=["GET"])
def list_conversations():
    result = _require_user()
    if result:
        return result

    conn = None
    try:
        conn = get_db_connection()
        rows = _list_threads(conn, g.current_user_row["id"])
        return jsonify({"threads": [_serialize_thread(row) for row in rows]})
    except Exception as exc:
        _log_exception("Error listing conversations", exc)
        return _json_error("Failed to load conversations.", 500, "conversation_list_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations", methods=["POST"])
def create_conversation():
    result = _require_user()
    if result:
        return result
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    title = payload.get("title")

    conn = None
    try:
        conn = get_db_connection()
        thread = _create_thread(conn, g.current_user_row["id"], title)
        conn.commit()
        return jsonify({"thread": _serialize_thread(thread)}), 201
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error creating conversation", exc)
        return _json_error("Failed to create conversation.", 500, "conversation_create_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations/<thread_id>", methods=["PATCH"])
def update_conversation(thread_id: str):
    result = _require_user()
    if result:
        return result
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    title = payload.get("title")
    archived = payload.get("archived") if "archived" in payload else None

    conn = None
    try:
        conn = get_db_connection()
        thread = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        if not thread:
            return _json_error("Conversation not found.", 404, "conversation_not_found")
        final_title = None
        if title is not None:
            final_title = title.strip()
            if not final_title:
                return _json_error("Title cannot be empty.", 400, "invalid_title")
        _update_thread(conn, thread_id, title=final_title, archived=bool(archived) if archived is not None else None)
        conn.commit()
        refreshed = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        return jsonify({"thread": _serialize_thread(refreshed)})
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error updating conversation", exc)
        return _json_error("Failed to update conversation.", 500, "conversation_update_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations/<thread_id>", methods=["DELETE"])
def delete_conversation(thread_id: str):
    result = _require_user()
    if result:
        return result
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    conn = None
    try:
        conn = get_db_connection()
        thread = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        if not thread:
            return _json_error("Conversation not found.", 404, "conversation_not_found")
        _soft_delete_thread(conn, thread_id)
        conn.commit()
        return jsonify({"ok": True})
    except Exception as exc:
        if conn:
            conn.rollback()
        _log_exception("Error deleting conversation", exc)
        return _json_error("Failed to delete conversation.", 500, "conversation_delete_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations/<thread_id>/messages", methods=["GET"])
def get_conversation_messages(thread_id: str):
    result = _require_user()
    if result:
        return result

    limit = max(1, min(request.args.get("limit", 50, type=int), 100))
    before = request.args.get("before")

    conn = None
    try:
        conn = get_db_connection()
        thread = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        if not thread:
            return _json_error("Conversation not found.", 404, "conversation_not_found")
        messages = _fetch_messages(conn, thread_id, limit=limit, before=before)
        return jsonify({
            "thread": _serialize_thread(thread),
            "messages": [_serialize_message(row) for row in messages],
        })
    except Exception as exc:
        _log_exception("Error fetching messages", exc)
        return _json_error("Failed to load messages.", 500, "conversation_messages_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations/<thread_id>/messages", methods=["POST"])
def post_conversation_message(thread_id: str):
    result = _require_user()
    if result:
        return result
    limited = _rate_limit_or_none(
        "chat",
        g.session_id or g.current_user_row["id"],
        Config.RATE_LIMIT_CHAT_PER_SESSION,
        Config.RATE_LIMIT_CHAT_WINDOW_SECONDS,
    )
    if limited:
        return limited
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    message_error = _validate_message(message)
    if message_error:
        return message_error

    conn = None
    emergency_decision = None
    detector_latency_ms = 0.0
    try:
        reset_request_metrics()
        conn = get_db_connection()
        thread = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        if not thread:
            return _json_error("Conversation not found.", 404, "conversation_not_found")
        emergency_decision, detector_latency_ms = _detect_emergency(message)
        event_context = None
        event_resolution = None
        history_rows: List[Dict[str, Any]] = []
        conversation_history: List[Dict[str, str]] = []
        state_envelope = _load_thread_state_envelope(thread.get("thread_state_json"))
        retrieval_cache = state_envelope.retrieval_cache
        conversation_state = state_envelope.conversation_state
        relationship = None
        dimensions = None
        active_event_result = None
        state_aware_event_result = None
        event_query_plan = None
        stale_active_event = False
        # Emergency detection uses only the current message. Skip history load so
        # prior turns cannot influence response wiring; title uses the thread row.
        if not emergency_decision.is_active_emergency:
            history_rows = _fetch_recent_history(conn, thread_id, limit=20)
            conversation_history = _conversation_history_from_rows(history_rows)
            try:
                event_context = _validate_optional_event_context(payload)
            except EventContextValidationError as exc:
                print(f"Rejected invalid UI event context: {exc}", file=sys.stderr)
                return _json_error("Invalid UI context.", 400, "invalid_ui_context")
            event_resolution = _resolve_validated_event_context(conn, event_context)
            relationship = _classify_relationship_shadow(
                message,
                history_rows,
                thread_id=thread_id,
                authoritative_active_event=conversation_state.active_entity is not None,
            )
            dimensions = _extract_dimensions_shadow(
                message,
                thread_id=thread_id,
                conversation_state=conversation_state,
                event_list_context=(
                    _explicit_event_list_request(message)
                    or bool(
                        conversation_state.active_topic is CanonicalTopic.EVENTS
                        and relationship
                        and relationship.relationship
                        in {
                            ConversationRelationship.REFINEMENT,
                            ConversationRelationship.FOLLOW_UP,
                        }
                    )
                ),
            )
            if event_resolution is None:
                active_event_result, stale_active_event = _resolve_active_event_request(
                    conn,
                    message,
                    state=conversation_state,
                    relationship=relationship,
                    retrieval_cache=retrieval_cache,
                )
                if active_event_result is None:
                    state_aware_event_result, event_query_plan = _state_aware_event_agent_result(
                        conn,
                        message=message,
                        state=conversation_state,
                        relationship=relationship,
                        dimensions=dimensions,
                        retrieval_cache=retrieval_cache,
                    )
        conn.close()
        conn = None

        # Run the expensive agent path outside any open DB transaction so
        # long LLM/sql execution does not hold row locks on the conversation.
        if emergency_decision.is_active_emergency:
            agent_result = _emergency_agent_result(
                emergency_decision,
                retrieval_cache,
                detector_latency_ms=detector_latency_ms,
            )
        elif active_event_result is not None or state_aware_event_result is not None:
            agent_result = active_event_result or state_aware_event_result
            _log_agent_metrics(
                mode=agent_result["mode"],
                thread_id=thread_id,
                streaming=False,
                extra={
                    "active_event_followup": active_event_result is not None,
                    "state_aware_event_query": state_aware_event_result is not None,
                    "llm_calls": 0,
                },
            )
        else:
            agent_result = _execute_agent_response_with_event_context(
                message,
                conversation_history=conversation_history,
                retrieval_cache=retrieval_cache,
                event_resolution=event_resolution,
                thread_id=thread_id,
                emergency_decision=emergency_decision,
            )
        answer = agent_result["answer"]
        mode = agent_result["mode"]
        sources = agent_result["sources"]
        next_cache = agent_result["retrieval_cache"]
        resolved_entity = agent_result.get("resolved_entity")
        conversation_transition = (
            _emergency_conversation_transition(conversation_state)
            if emergency_decision.is_active_emergency
            else _plan_conversation_transition(
                state=conversation_state,
                relationship=relationship,
                dimensions=dimensions,
                message=message,
                mode=mode,
                agent_result=agent_result,
                selected_resolution=event_resolution,
                stale_active_event=stale_active_event,
                event_query_plan=event_query_plan,
                event_retrieval_matched=(
                    agent_result.get("result", {}).get("event_retrieval_matched")
                    if event_query_plan is not None
                    else None
                ),
            )
        )
        transition = conversation_transition.state_proposal(conversation_state)
        persisted_cache, invalidate_retrieval_cache = _cache_for_conversation_transition(
            current_cache=retrieval_cache,
            next_cache=next_cache,
            transition=conversation_transition,
            replacement_cache_valid=bool(
                agent_result.get("event_cache_replacement_valid")
            ),
        )

        persisted = _persist_conversation_turn(
            thread_id=thread_id,
            user_id=g.current_user_row["id"],
            session_id=g.session_row["session_id"],
            expected_envelope=state_envelope,
            next_retrieval_cache=persisted_cache,
            transition=transition,
            invalidate_retrieval_cache=invalidate_retrieval_cache,
            user_content=message,
            user_meta=_user_message_metadata(
                event_context=event_context,
                relationship=relationship,
                dimensions=dimensions,
                transition=conversation_transition,
                active_event_behavior_applied=active_event_result is not None,
                emergency_decision=emergency_decision,
                event_query_plan=event_query_plan,
            ),
            assistant_content=answer,
            assistant_mode=mode,
            assistant_sources=sources,
            assistant_model_name=resolve_execution_model_name(
                agent_result,
                configured_model=os.getenv("GEMINI_MODEL", ""),
            ),
            resolved_entity=resolved_entity,
            set_initial_title=(
                False
                if emergency_decision.is_active_emergency
                else len(history_rows) == 0
            ),
            interaction_log_enabled=not emergency_decision.is_active_emergency,
            expected_last_message_at=(
                thread.get("last_message_at")
                if emergency_decision.is_active_emergency
                else None
            ),
        )
        if emergency_decision.is_active_emergency:
            _log_emergency_metrics(
                emergency_decision,
                thread_id=thread_id,
                streaming=False,
                success=True,
                detector_latency_ms=detector_latency_ms,
            )

        response_payload = {
            "thread": _serialize_thread(persisted["thread"]),
            "user_message": _serialize_message(persisted["user_message"]),
            "assistant_message": _serialize_message(persisted["assistant_message"]),
        }
        if resolved_entity:
            response_payload["resolved_entity"] = resolved_entity
        return jsonify(response_payload), 201
    except ConversationConflictError:
        if emergency_decision is not None and emergency_decision.is_active_emergency:
            _log_emergency_metrics(
                emergency_decision,
                thread_id=thread_id,
                streaming=False,
                success=False,
                detector_latency_ms=detector_latency_ms,
                failure_code="conversation_conflict",
            )
        return _json_error(
            CONVERSATION_CONFLICT_MESSAGE,
            409,
            "conversation_conflict",
        )
    except Exception as exc:
        if conn:
            conn.rollback()
        if emergency_decision is not None and emergency_decision.is_active_emergency:
            _log_emergency_metrics(
                emergency_decision,
                thread_id=thread_id,
                streaming=False,
                success=False,
                detector_latency_ms=detector_latency_ms,
                failure_code="persistence_failed",
            )
        _log_exception("Error posting conversation message", exc)
        return _json_error("Failed to send message.", 500, "conversation_message_failed")
    finally:
        if conn:
            conn.close()


@app.route("/conversations/<thread_id>/messages/stream", methods=["POST"])
def post_conversation_message_stream(thread_id: str):
    """Streamed variant of POST /conversations/<id>/messages (Server-Sent Events).

    Emits `data: {"type":"delta","text":...}` events as the answer is produced,
    then a final `data: {"type":"final", ...}` with the persisted thread/messages
    (or `{"type":"error", ...}`). The non-streaming endpoint remains the default.
    """
    if not Config.STREAMING_ENABLED:
        return _json_error("Streaming is disabled.", 404, "streaming_disabled")

    result = _require_user()
    if result:
        return result
    limited = _rate_limit_or_none(
        "chat",
        g.session_id or g.current_user_row["id"],
        Config.RATE_LIMIT_CHAT_PER_SESSION,
        Config.RATE_LIMIT_CHAT_WINDOW_SECONDS,
    )
    if limited:
        return limited
    csrf_error = _enforce_csrf()
    if csrf_error:
        return csrf_error

    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    message_error = _validate_message(message)
    if message_error:
        return message_error

    # Load context up front, then release the DB before the long LLM work.
    conn = None
    emergency_decision = None
    detector_latency_ms = 0.0
    try:
        reset_request_metrics()
        conn = get_db_connection()
        thread = _fetch_thread(conn, g.current_user_row["id"], thread_id)
        if not thread:
            return _json_error("Conversation not found.", 404, "conversation_not_found")
        emergency_decision, detector_latency_ms = _detect_emergency(message)
        event_context = None
        event_resolution = None
        history_rows: List[Dict[str, Any]] = []
        conversation_history: List[Dict[str, str]] = []
        state_envelope = _load_thread_state_envelope(thread.get("thread_state_json"))
        retrieval_cache = state_envelope.retrieval_cache
        conversation_state = state_envelope.conversation_state
        relationship = None
        dimensions = None
        active_event_result = None
        state_aware_event_result = None
        event_query_plan = None
        stale_active_event = False
        # Emergency detection uses only the current message. Skip history load so
        # prior turns cannot influence response wiring; title uses the thread row.
        if not emergency_decision.is_active_emergency:
            history_rows = _fetch_recent_history(conn, thread_id, limit=20)
            conversation_history = _conversation_history_from_rows(history_rows)
            try:
                event_context = _validate_optional_event_context(payload)
            except EventContextValidationError as exc:
                print(f"Rejected invalid UI event context: {exc}", file=sys.stderr)
                return _json_error("Invalid UI context.", 400, "invalid_ui_context")
            event_resolution = _resolve_validated_event_context(conn, event_context)
            relationship = _classify_relationship_shadow(
                message,
                history_rows,
                thread_id=thread_id,
                authoritative_active_event=conversation_state.active_entity is not None,
            )
            dimensions = _extract_dimensions_shadow(
                message,
                thread_id=thread_id,
                conversation_state=conversation_state,
                event_list_context=(
                    _explicit_event_list_request(message)
                    or bool(
                        conversation_state.active_topic is CanonicalTopic.EVENTS
                        and relationship
                        and relationship.relationship
                        in {
                            ConversationRelationship.REFINEMENT,
                            ConversationRelationship.FOLLOW_UP,
                        }
                    )
                ),
            )
            if event_resolution is None:
                active_event_result, stale_active_event = _resolve_active_event_request(
                    conn,
                    message,
                    state=conversation_state,
                    relationship=relationship,
                    retrieval_cache=retrieval_cache,
                )
                if active_event_result is None:
                    state_aware_event_result, event_query_plan = _state_aware_event_agent_result(
                        conn,
                        message=message,
                        state=conversation_state,
                        relationship=relationship,
                        dimensions=dimensions,
                        retrieval_cache=retrieval_cache,
                    )
    except Exception as exc:
        _log_exception("Error preparing streamed message", exc)
        return _json_error("Failed to send message.", 500, "conversation_message_failed")
    finally:
        if conn:
            conn.close()

    # Capture request-scoped values so the generator does not depend on the
    # request context once streaming begins.
    user_id = g.current_user_row["id"]
    session_id = g.session_row["session_id"]
    is_first_message = len(history_rows) == 0
    existing_title = thread["title"]
    emergency_request = emergency_decision.is_active_emergency

    def _sse(obj: Dict[str, Any]) -> str:
        return f"data: {json_dumps(obj)}\n\n"

    def _generate():
        reset_request_metrics()
        full_answer = ""
        mode = "hybrid"
        result_payload: Dict[str, Any] = {}
        next_cache = retrieval_cache
        selected_request = event_resolution is not None
        active_event_request = active_event_result is not None
        state_aware_event_request = state_aware_event_result is not None
        deterministic_request = (
            emergency_request
            or selected_request
            or active_event_request
            or state_aware_event_request
        )
        semantic_eligible = (
            False
            if deterministic_request
            else _semantic_cache_eligible(message, conversation_history)
        )
        cache_hit = False
        cached_sources: List[Dict[str, str]] = []
        selected_sources: List[Dict[str, str]] = []
        resolved_entity: Optional[Dict[str, str]] = None
        execution_metadata: Dict[str, Any] = {}
        deterministic_result: Optional[Dict[str, Any]] = None
        generation_degraded = False

        try:
            yield ": keepalive\n\n"
            if emergency_request:
                deterministic_result = _emergency_agent_result(
                    emergency_decision,
                    retrieval_cache,
                    detector_latency_ms=detector_latency_ms,
                )
            elif active_event_request:
                deterministic_result = active_event_result
            elif state_aware_event_request:
                deterministic_result = state_aware_event_result
            elif selected_request:
                deterministic_result = _selected_event_agent_result(
                    message,
                    event_resolution,
                    retrieval_cache,
                )
            if deterministic_result is not None:
                full_answer = deterministic_result["answer"]
                mode = deterministic_result["mode"]
                result_payload = deterministic_result["result"]
                next_cache = deterministic_result["retrieval_cache"]
                selected_sources = deterministic_result["sources"]
                resolved_entity = deterministic_result.get("resolved_entity")
                for i, word in enumerate(full_answer.split()):
                    yield _sse({"type": "delta", "text": word if i == 0 else f" {word}"})
            else:
                cached = _semantic_cache.get(message) if semantic_eligible else None
            if (
                not deterministic_request
                and cached
                and _is_cacheable_semantic_answer(cached.get("answer", ""))
            ):
                cache_hit = True
                full_answer = cached.get("answer", "")
                mode = cached.get("mode", "semantic_cache")
                result_payload = cached.get("agent_result", {"answer": full_answer})
                cached_sources = cached.get("sources", [])
                execution_metadata["execution_degraded"] = bool(
                    cached.get("execution_degraded")
                )
                for i, word in enumerate(full_answer.split()):
                    yield _sse({"type": "delta", "text": word if i == 0 else f" {word}"})
            elif not deterministic_request:
                streamed_text = ""
                for item in _iter_stream_agent_with_keepalive(
                    message, conversation_history, retrieval_cache,
                ):
                    if item is None:
                        yield ": keepalive\n\n"
                        continue
                    kind, data = item
                    if kind == "delta":
                        streamed_text += data
                        yield _sse({"type": "delta", "text": data})
                    elif kind == "final":
                        full_answer = data.get("answer", streamed_text)
                        mode = data.get("mode", "hybrid")
                        result_payload = data.get("result", {})
                        next_cache = data.get("retrieval_cache", retrieval_cache)
                        pipeline_metrics = data.get("pipeline_metrics")
                        if isinstance(pipeline_metrics, dict):
                            execution_metadata.update(pipeline_metrics)
                        if "model_name" in data:
                            execution_metadata["model_name"] = data.get("model_name")
                    elif kind == "_execution_metrics":
                        llm_calls = max(0, int(data.get("llm_calls", 0)))
                        execution_metadata["llm_calls"] = llm_calls
                        increment_llm_call_counter(llm_calls)
                if not full_answer and streamed_text:
                    full_answer = streamed_text
            if not full_answer and not cache_hit:
                yield _sse({"type": "error", "error": "Generation failed."})
                return
        except Exception as exc:  # noqa: BLE001
            if full_answer.strip():
                _log_exception("Stream ended with partial answer after error", exc)
                generation_degraded = True
            else:
                _log_exception("Error during streamed generation", exc)
                yield _sse({"type": "error", "error": _stream_generation_error(exc)})
                return

        if full_answer and not deterministic_request:
            enriched = apply_post_stream_fallback(
                message,
                full_answer,
                mode,
                prepared_intent=result_payload.get("prepared_intent"),
                conversation_history=conversation_history,
            )
            if enriched != full_answer:
                yield _sse({"type": "correction", "text": enriched})
                full_answer = enriched
            if (
                semantic_eligible
                and mode != "semantic_cache"
                and not execution_metadata.get("execution_degraded")
                and _is_cacheable_semantic_answer(full_answer)
            ):
                sources_for_cache = extract_sources(mode, result_payload)
                _semantic_cache.put(
                    message,
                    {
                        "answer": full_answer,
                        "mode": mode,
                        "sources": sources_for_cache,
                        "agent_result": result_payload,
                        "execution_degraded": False,
                    },
                )

        try:
            if deterministic_request:
                sources = selected_sources
            else:
                sources = (
                    cached_sources
                    if cache_hit and cached_sources
                    else extract_sources(mode, result_payload)
                )
            transition_result = deterministic_result or {
                "answer": full_answer,
                "mode": mode,
                "sources": sources,
                "result": result_payload,
                "retrieval_cache": next_cache,
                "execution_degraded": bool(execution_metadata.get("execution_degraded")),
            }
            conversation_transition = (
                _emergency_conversation_transition(conversation_state)
                if emergency_request
                else _plan_conversation_transition(
                    state=conversation_state,
                    relationship=relationship,
                    dimensions=dimensions,
                    message=message,
                    mode=mode,
                    agent_result=transition_result,
                    selected_resolution=event_resolution,
                    stale_active_event=stale_active_event,
                    event_query_plan=event_query_plan,
                    event_retrieval_matched=(
                        transition_result.get("result", {}).get("event_retrieval_matched")
                        if event_query_plan is not None
                        else None
                    ),
                    partial=generation_degraded,
                )
            )
            transition = conversation_transition.state_proposal(conversation_state)
            persisted_cache, invalidate_retrieval_cache = _cache_for_conversation_transition(
                current_cache=retrieval_cache,
                next_cache=next_cache,
                transition=conversation_transition,
                replacement_cache_valid=bool(
                    transition_result.get("event_cache_replacement_valid")
                ),
            )
            persisted = _persist_conversation_turn(
                thread_id=thread_id,
                user_id=user_id,
                session_id=session_id,
                expected_envelope=state_envelope,
                next_retrieval_cache=persisted_cache,
                transition=transition,
                invalidate_retrieval_cache=invalidate_retrieval_cache,
                user_content=message,
                user_meta=_user_message_metadata(
                    event_context=event_context,
                    relationship=relationship,
                    dimensions=dimensions,
                    transition=conversation_transition,
                    active_event_behavior_applied=active_event_request,
                    emergency_decision=emergency_decision,
                    event_query_plan=event_query_plan,
                ),
                assistant_content=full_answer,
                assistant_mode=mode,
                assistant_sources=sources,
                assistant_model_name=resolve_execution_model_name(
                    (
                        deterministic_result
                        if deterministic_request
                        else execution_metadata
                    ),
                    configured_model=os.getenv("GEMINI_MODEL", ""),
                    llm_calls=(
                        0
                        if deterministic_request or cache_hit
                        else execution_metadata.get("llm_calls", get_llm_calls())
                    ),
                ),
                resolved_entity=resolved_entity,
                set_initial_title=(
                    False
                    if emergency_request
                    else (
                        existing_title == "New conversation" and is_first_message
                    )
                ),
                interaction_log_enabled=not emergency_request,
                expected_last_message_at=(
                    thread.get("last_message_at") if emergency_request else None
                ),
            )
            final_payload = {
                "type": "final",
                "answer": full_answer,
                "mode": mode,
                "sources": sources,
                "thread": _serialize_thread(persisted["thread"]),
                "user_message": _serialize_message(persisted["user_message"]),
                "assistant_message": _serialize_message(persisted["assistant_message"]),
            }
            if resolved_entity:
                final_payload["resolved_entity"] = resolved_entity
            if emergency_request:
                _log_emergency_metrics(
                    emergency_decision,
                    thread_id=thread_id,
                    streaming=True,
                    success=True,
                    detector_latency_ms=detector_latency_ms,
                )
            yield _sse(final_payload)

            # Post-final Boston.gov correction (bounded wait; does not block first token).
            if (
                full_answer
                and not deterministic_request
                and Config.BOSTON_GOV_ASYNC_ENABLED
                and not Config.BOSTON_GOV_BLOCKING
                and should_apply_boston_gov_fallback(message, full_answer, mode)
            ):
                enriched = try_boston_gov_enrichment(
                    message,
                    full_answer,
                    timeout_seconds=Config.BOSTON_GOV_ASYNC_TIMEOUT_SECONDS,
                )
                if enriched != full_answer:
                    yield _sse({"type": "correction", "text": enriched})
                    correction_conn = None
                    cursor = None
                    try:
                        correction_conn = get_db_connection()
                        cursor = correction_conn.cursor()
                        cursor.execute(
                            "UPDATE conversation_messages SET content = %s WHERE id = %s",
                            (enriched, persisted["assistant_message"]["id"]),
                        )
                        correction_conn.commit()
                    except Exception as exc:  # noqa: BLE001
                        if correction_conn:
                            try:
                                correction_conn.rollback()
                            except Exception:
                                pass
                        _log_exception("Failed to persist Boston.gov correction", exc)
                    finally:
                        if cursor:
                            cursor.close()
                        if correction_conn:
                            correction_conn.close()
                    full_answer = enriched
            if not emergency_request:
                _log_agent_metrics(
                    mode=mode,
                    cache_hit=cache_hit,
                    thread_id=thread_id,
                    streaming=True,
                    extra=(
                            {"ui_event_resolution": event_resolution.status}
                            if event_resolution is not None
                            else ({"active_event_followup": True} if active_event_request else None)
                        ),
                )
        except ConversationConflictError:
            if emergency_request:
                _log_emergency_metrics(
                    emergency_decision,
                    thread_id=thread_id,
                    streaming=True,
                    success=False,
                    detector_latency_ms=detector_latency_ms,
                    failure_code="conversation_conflict",
                )
            yield _sse({
                "type": "error",
                "error": CONVERSATION_CONFLICT_MESSAGE,
                "code": "conversation_conflict",
            })
        except Exception as exc:  # noqa: BLE001
            if emergency_request:
                _log_emergency_metrics(
                    emergency_decision,
                    thread_id=thread_id,
                    streaming=True,
                    success=False,
                    detector_latency_ms=detector_latency_ms,
                    failure_code="persistence_failed",
                )
            _log_exception("Error persisting streamed message", exc)
            yield _sse({"type": "error", "error": "Failed to save message."})

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Legacy compatibility endpoints
# =============================================================================
@app.route("/chat", methods=["POST"])
def chat():
    result = _require_api_key_or_user()
    if result:
        return result

    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    conversation_history = data.get("conversation_history", [])
    message_error = _validate_message(message)
    if message_error:
        return message_error
    reset_request_metrics()
    emergency_decision, detector_latency_ms = _detect_emergency(message)

    session_id = g.get("session_id") or str(uuid.uuid4())
    if g.get("api_key_authenticated") and "session_id" not in session:
        session.permanent = True
        session["session_id"] = session_id
    elif g.get("api_key_authenticated"):
        session_id = session.get("session_id")

    if emergency_decision.is_active_emergency:
        agent_result = _emergency_agent_result(
            emergency_decision,
            create_empty_cache(),
            detector_latency_ms=detector_latency_ms,
        )
        _log_emergency_metrics(
            emergency_decision,
            thread_id=None,
            streaming=False,
            success=True,
            detector_latency_ms=detector_latency_ms,
        )
        return jsonify(
            {
                "session_id": session_id,
                "response": agent_result["answer"],
                "sources": [],
                "mode": "emergency",
                "log_id": None,
            }
        )

    try:
        event_context = _validate_optional_event_context(data)
    except EventContextValidationError as exc:
        print(f"Rejected invalid UI event context: {exc}", file=sys.stderr)
        return _json_error("Invalid UI context.", 400, "invalid_ui_context")

    retrieval_cache = _session_cache.get(session_id)

    conn = None
    try:
        event_resolution = None
        if event_context is not None:
            conn = get_db_connection()
            event_resolution = _resolve_validated_event_context(conn, event_context)
            conn.close()
            conn = None
        agent_result = _execute_agent_response_with_event_context(
            message,
            conversation_history=conversation_history,
            retrieval_cache=retrieval_cache,
            event_resolution=event_resolution,
            emergency_decision=emergency_decision,
        )
        _session_cache.set(session_id, agent_result["retrieval_cache"])
        log_id = log_interaction(
            session_id=session_id,
            client_query=message,
            app_response=agent_result["answer"],
            mode=agent_result["mode"],
            user_id=g.current_user_row["id"] if g.get("current_user_row") else None,
        )
        response_payload = {
            "session_id": session_id,
            "response": agent_result["answer"],
            "sources": agent_result["sources"],
            "mode": agent_result["mode"],
            "log_id": log_id,
        }
        if agent_result.get("resolved_entity"):
            response_payload["resolved_entity"] = agent_result["resolved_entity"]
        return jsonify(response_payload)
    except Exception as exc:
        _log_exception("Error in /chat", exc)
        return _json_error("Internal server error.", 500, "chat_failed")
    finally:
        if conn:
            conn.close()


@app.route("/log", methods=["POST", "PUT"])
def log_endpoint():
    result = _require_api_key_or_user()
    if result:
        return result
    if request.method in {"POST", "PUT"} and g.get("current_user_row"):
        csrf_error = _enforce_csrf()
        if csrf_error:
            return csrf_error

    data = request.get_json() or {}
    session_id = g.get("session_id") or (g.session_row["session_id"] if g.get("session_row") else str(uuid.uuid4()))

    if request.method == "POST":
        client_query = data.get("client_query", "")
        app_response = data.get("app_response", "")
        mode = data.get("mode", "")
        if not client_query:
            return _json_error("client_query is required", 400, "missing_client_query")
        log_id = log_interaction(
            session_id=session_id,
            client_query=client_query,
            app_response=app_response,
            mode=mode,
            user_id=g.current_user_row["id"] if g.get("current_user_row") else None,
            thread_id=data.get("thread_id"),
            message_id=data.get("message_id"),
        )
        if not log_id:
            return _json_error("Failed to create log entry", 500, "log_create_failed")
        return jsonify({"log_id": log_id, "message": "Log entry created"}), 201

    log_id = data.get("log_id")
    if not log_id:
        return _json_error("log_id is required", 400, "missing_log_id")
    owner_user_id = g.current_user_row["id"] if g.get("current_user_row") else None
    conn = None
    try:
        conn = get_db_connection()
        if not _verify_log_ownership(
            conn,
            int(log_id),
            user_id=owner_user_id,
            session_id=session_id,
        ):
            return _json_error("Not allowed to update this log entry.", 403, "log_forbidden")
    finally:
        if conn:
            conn.close()

    updated_id = log_interaction(
        session_id=session_id,
        client_query="",
        app_response="",
        log_id=log_id,
        rating=data.get("client_response_rating", ""),
        flag_reason=data.get("flag_reason", ""),
        flag_details=data.get("flag_details", ""),
        user_id=owner_user_id,
    )
    if updated_id == "forbidden":
        return _json_error("Not allowed to update this log entry.", 403, "log_forbidden")
    if not updated_id:
        return _json_error("Failed to update log entry", 500, "log_update_failed")
    return jsonify({"log_id": updated_id, "message": "Log entry updated"})


@app.route("/events", methods=["GET"])
def events():
    result = _require_api_key_or_user()
    if result:
        return result

    limit = max(1, min(request.args.get("limit", 10, type=int), 100))
    days_ahead = max(1, min(request.args.get("days_ahead", 7, type=int), 365))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        from materialized_views import events_query_source, is_materialized_views_enabled

        source_table = events_query_source(conn)
        url_col = ", event_url" if source_table == "events_materialized" else ""
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"""
            SELECT id, event_name, event_date, start_date, end_date,
                   start_time, end_time, raw_text, source_pdf{url_col}
            FROM {source_table}
            WHERE start_date >= CURDATE()
              AND start_date <= DATE_ADD(CURDATE(), INTERVAL %s DAY)
            ORDER BY start_date ASC, start_time ASC
            LIMIT %s
            """,
            (days_ahead, limit),
        )
        rows = cursor.fetchall()
        events_list = []
        for row in rows:
            event_url = row.get("event_url") if source_table == "events_materialized" else None
            if not event_url and row.get("raw_text"):
                match = re.search(
                    r"URL:\s*(https?://\S+)", str(row["raw_text"]), re.IGNORECASE,
                )
                if match:
                    event_url = match.group(1).rstrip(").,]")
            events_list.append(
                {
                    "id": row["id"],
                    "event_name": row["event_name"],
                    "event_date": row["event_date"],
                    "start_date": str(row["start_date"]) if row["start_date"] else None,
                    "end_date": str(row["end_date"]) if row["end_date"] else None,
                    "start_time": str(row["start_time"]) if row["start_time"] else None,
                    "end_time": str(row["end_time"]) if row["end_time"] else None,
                    "description": row["raw_text"],
                    "source": row["source_pdf"],
                    "url": event_url,
                }
            )
        return jsonify({
            "events": events_list,
            "total": len(events_list),
            "source_table": source_table,
            "materialized": is_materialized_views_enabled(),
        })
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) == 1146:
            return jsonify({"events": [], "total": 0})
        _log_exception("Error in /events", exc)
        return _json_error("Failed to fetch events.", 500, "events_failed")
    except Exception as exc:
        _log_exception("Error in /events", exc)
        return _json_error("Failed to fetch events.", 500, "events_failed")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/health", methods=["GET"])
def health():
    status = {
        "status": "ok",
        "version": Config.API_VERSION,
        "cache_backend": Config.CACHE_BACKEND,
        "production_mode": Config.PRODUCTION_MODE,
        "semantic_cache_enabled": Config.SEMANTIC_CACHE_ENABLED,
        "semantic_cache_backend": Config.SEMANTIC_CACHE_BACKEND,
        "prepared_sql_enabled": Config.PREPARED_SQL_ENABLED,
        "intent_router_v2": Config.INTENT_ROUTER_V2_ENABLED,
        "boston_gov_async": Config.BOSTON_GOV_ASYNC_ENABLED,
        "embed_provider": Config.EMBED_PROVIDER,
        "local_embed_model": Config.LOCAL_EMBED_MODEL,
        "vector_db_provider": Config.VECTOR_DB_PROVIDER,
        "bm25_hybrid_enabled": Config.BM25_HYBRID_ENABLED,
        "qdrant_collection": (
            Config.QDRANT_COLLECTION if Config.VECTOR_DB_PROVIDER == "qdrant" else None
        ),
        "vectordb_dir": str(_retrieval.VECTORDB_DIR) if _retrieval else None,
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        "gemini_fast_model": os.getenv("GEMINI_FAST_MODEL", "gemini-2.5-flash-lite"),
    }
    try:
        conn = get_db_connection()
        conn.close()
        status["database"] = "connected"
    except Exception:
        status["database"] = "disconnected"
        status["status"] = "degraded"

    status["shared_cache"] = _check_shared_cache_health()
    if Config.CACHE_BACKEND == "mysql" and status["shared_cache"] == "disconnected":
        status["status"] = "degraded"
    elif Config.PRODUCTION_MODE and Config.CACHE_BACKEND != "mysql":
        status["status"] = "degraded"

    status["chroma"] = _check_chroma_health()
    status["qdrant"] = _check_qdrant_health()
    if Config.VECTOR_DB_PROVIDER == "qdrant":
        if status["qdrant"] in {"missing", "unavailable", "degraded", "empty"}:
            status["status"] = "degraded"
    elif status["chroma"] in {"missing", "unavailable", "degraded"}:
        status["status"] = "degraded"

    ingest = check_ingestion_lag(
        repo_root=_ROOT_DIR,
        stale_hours=Config.INGEST_STALE_HOURS,
    )
    status["ingestion"] = ingest
    if ingest.get("status") in {"stale", "degraded"}:
        status["status"] = "degraded"

    try:
        feed_311 = check_311_feed_balance(get_db_connection)
        status["boston_311_feeds"] = feed_311
        if feed_311.get("status") == "degraded":
            status["status"] = "degraded"
    except Exception:
        pass

    try:
        from ingest_queue import check_311_freshness, get_ingest_job_status

        status["ingest_jobs"] = get_ingest_job_status(
            connection_factory=get_db_connection
        )
        status["boston_311_freshness"] = check_311_freshness(
            get_db_connection,
            stale_hours=Config.BOSTON_311_STALE_HOURS,
        )
        if status["boston_311_freshness"].get("status") == "stale":
            status["status"] = "degraded"
    except Exception:
        pass

    status["materialized_views_enabled"] = Config.MATERIALIZED_VIEWS_ENABLED

    _, prod_issues = validate_production_config(
        production_mode=Config.PRODUCTION_MODE,
        cache_backend=Config.CACHE_BACKEND,
        semantic_cache_enabled=Config.SEMANTIC_CACHE_ENABLED,
        gemini_model=os.getenv("GEMINI_MODEL", ""),
        gemini_fast_model=os.getenv("GEMINI_FAST_MODEL", ""),
        embed_provider=Config.EMBED_PROVIDER,
        vectordb_dir=Path(_retrieval.VECTORDB_DIR) if _retrieval else None,
        vector_db_provider=Config.VECTOR_DB_PROVIDER,
    )
    if prod_issues:
        status["config_warnings"] = prod_issues

    return jsonify(status)


# =============================================================================
# Admin endpoints
# =============================================================================
@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    result = _require_admin()
    if result:
        return result

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS cnt FROM interaction_log")
        total = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) AS cnt FROM interaction_log WHERE flagged = TRUE")
        total_flagged = cursor.fetchone()["cnt"]

        cursor.execute(
            """
            SELECT COUNT(*) AS cnt FROM interaction_log
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            """
        )
        this_week = cursor.fetchone()["cnt"]

        cursor.execute(
            """
            SELECT COUNT(*) AS cnt FROM interaction_log
            WHERE app_response LIKE %s
               OR app_response LIKE %s
               OR app_response LIKE %s
            """,
            ("%No results found%", "%couldn't find%", "%no data%"),
        )
        no_results = cursor.fetchone()["cnt"]

        cursor.execute(
            """
            SELECT data_selected AS mode, COUNT(*) AS cnt
            FROM interaction_log
            WHERE data_selected IS NOT NULL AND data_selected != ''
            GROUP BY data_selected
            ORDER BY cnt DESC
            """
        )
        mode_breakdown = {row["mode"]: row["cnt"] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT flag_reason, COUNT(*) AS cnt
            FROM interaction_log
            WHERE flagged = TRUE AND flag_reason IS NOT NULL
            GROUP BY flag_reason
            ORDER BY cnt DESC
            """
        )
        flag_reasons = {row["flag_reason"]: row["cnt"] for row in cursor.fetchall()}

        return jsonify(
            {
                "total_interactions": total,
                "total_flagged": total_flagged,
                "interactions_this_week": this_week,
                "no_result_count": no_results,
                "mode_breakdown": mode_breakdown,
                "flag_reasons": flag_reasons,
            }
        )
    except Exception as exc:
        return _json_error(str(exc), 500, "admin_stats_failed")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/flags", methods=["GET"])
def admin_flags():
    result = _require_admin()
    if result:
        return result

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, session_id, user_id, thread_id, client_query, app_response,
                   data_selected, flag_reason, flag_details, flagged_at, created_at, moderator_comment,
                   resolved, resolved_at
            FROM interaction_log
            WHERE flagged = TRUE
            ORDER BY flagged_at DESC
            LIMIT 200
            """
        )
        rows = cursor.fetchall()
        for row in rows:
            for key in ("flagged_at", "created_at", "resolved_at"):
                if row.get(key) and hasattr(row[key], "isoformat"):
                    row[key] = row[key].isoformat()
        return jsonify({"flags": rows})
    except Exception as exc:
        return _json_error(str(exc), 500, "admin_flags_failed")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/interactions", methods=["GET"])
def admin_interactions():
    result = _require_admin()
    if result:
        return result

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, session_id, user_id, thread_id, client_query, app_response,
                   data_selected, flagged, created_at
            FROM interaction_log
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        rows = cursor.fetchall()
        for row in rows:
            if row.get("created_at") and hasattr(row["created_at"], "isoformat"):
                row["created_at"] = row["created_at"].isoformat()
            if row.get("app_response") and len(row["app_response"]) > 200:
                row["app_response"] = row["app_response"][:200] + "..."
        return jsonify({"interactions": rows})
    except Exception as exc:
        return _json_error(str(exc), 500, "admin_interactions_failed")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/admin/no-results", methods=["GET"])
def admin_no_results():
    result = _require_admin()
    if result:
        return result

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, user_id, thread_id, client_query, data_selected, created_at
            FROM interaction_log
            WHERE app_response LIKE %s
               OR app_response LIKE %s
               OR app_response LIKE %s
               OR app_response LIKE %s
            ORDER BY created_at DESC
            LIMIT 100
            """,
            ("%No results found%", "%couldn't find%", "%no data%", "%I couldn't find%"),
        )
        rows = cursor.fetchall()
        for row in rows:
            if row.get("created_at") and hasattr(row["created_at"], "isoformat"):
                row["created_at"] = row["created_at"].isoformat()
        return jsonify({"no_results": rows})
    except Exception as exc:
        return _json_error(str(exc), 500, "admin_no_results_failed")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route("/admin/knowledge", methods=["GET"])
def admin_get_knowledge():
    result = _require_admin()
    if result:
        return result
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, content, category, expires_at, added_by, active, created_at
            FROM admin_knowledge
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        for row in rows:
            for key in ("expires_at", "created_at"):
                if row.get(key) and hasattr(row[key], "isoformat"):
                    row[key] = row[key].isoformat()
        return jsonify({"knowledge": rows})
    except Exception as e:
        return _json_error(str(e), 500, "admin_knowledge_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/knowledge", methods=["POST"])
def admin_add_knowledge():
    result = _require_admin_mutation()
    if result:
        return result
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return _json_error("content is required", 400, "missing_content")
    if len(content) > Config.MAX_COMMUNITY_NOTE_LENGTH:
        return _json_error(
            f"Content is too long (max {Config.MAX_COMMUNITY_NOTE_LENGTH} characters).",
            400,
            "content_too_long",
        )
    expires_at = data.get("expires_at") or (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
    ).strftime("%Y-%m-%d")
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO admin_knowledge (content, category, expires_at, source_flag_id, added_by)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            content,
            data.get("category", "general"),
            expires_at,
            data.get("source_flag_id"),
            g.current_user_row["username"] if g.get("current_user_row") else "admin",
        ))
        conn.commit()
        _trigger_ingest()
        return jsonify({"id": cursor.lastrowid, "message": "Knowledge entry added"}), 201
    except Exception as e:
        return _json_error(str(e), 500, "admin_knowledge_add_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/knowledge/<int:entry_id>", methods=["PUT"])
def admin_edit_knowledge(entry_id):
    result = _require_admin_mutation()
    if result:
        return result
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return _json_error("content is required", 400, "admin_knowledge_edit_invalid")
    if len(content) > Config.MAX_COMMUNITY_NOTE_LENGTH:
        return _json_error(
            f"Content is too long (max {Config.MAX_COMMUNITY_NOTE_LENGTH} characters).",
            400,
            "content_too_long",
        )
    category = data.get("category", "general")
    expires_at = data.get("expires_at") or None
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "UPDATE admin_knowledge SET content = %s, category = %s, expires_at = %s WHERE id = %s",
            (content, category, expires_at, entry_id),
        )
        conn.commit()
        _trigger_ingest()
        return jsonify({"message": "Note updated"})
    except Exception as e:
        return _json_error(str(e), 500, "admin_knowledge_edit_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/knowledge/<int:entry_id>", methods=["DELETE"])
def admin_deactivate_knowledge(entry_id):
    result = _require_admin_mutation()
    if result:
        return result
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("UPDATE admin_knowledge SET active = FALSE WHERE id = %s", (entry_id,))
        conn.commit()
        _remove_from_chroma(entry_id)
        return jsonify({"message": "Entry deactivated"})
    except Exception as e:
        return _json_error(str(e), 500, "admin_knowledge_deactivate_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/flags/<int:flag_id>/comment", methods=["PUT"])
def admin_comment_flag(flag_id):
    result = _require_admin_mutation()
    if result:
        return result
    data = request.get_json() or {}
    comment = data.get("moderator_comment", "").strip()
    resolved = data.get("resolved", False)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            UPDATE interaction_log
            SET moderator_comment = %s,
                resolved = %s,
                resolved_at = %s
            WHERE id = %s
        """, (
            comment,
            resolved,
            datetime.datetime.utcnow() if resolved else None,
            flag_id,
        ))
        conn.commit()
        return jsonify({"message": "Flag updated"})
    except Exception as e:
        return _json_error(str(e), 500, "admin_flag_comment_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

@app.route("/community/notes/chat", methods=["POST"])
def community_notes_chat():
    result = _require_api_key_or_user()
    if result:
        return result
    if g.get("current_user_row"):
        csrf_error = _enforce_csrf()
        if csrf_error:
            return csrf_error
    data = request.get_json() or {}
    messages = data.get("messages", [])
    if not messages:
        return _json_error("messages required", 400, "missing_messages")
    try:
        client = _get_llm_client()
        model = client.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"))
        system = (
            "You are helping a Dorchester community member report a local issue or share "
            "neighborhood information for a community chatbot.\n\n"
            "Ask short friendly follow-up questions ONE AT A TIME to gather useful details. "
            "Focus on: exact location (street, intersection, landmark), time/date, severity, "
            "who is affected, and any safety considerations.\n\n"
            "After 3-5 exchanges when you have enough detail, respond with EXACTLY this format "
            "and nothing else:\n"
            "READY:\n"
            "What: <one sentence describing the issue>\n"
            "Where: <specific location>\n"
            "When: <time or date>\n"
            "Details: <any extra context>\n\n"
            "Do not ask more than 5 questions. Keep questions short and friendly. "
            "Never mention AI, databases, or that you are compiling a note."
        )
        history = "\n".join([
            f"{'Community member' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in messages
        ])
        resp = model.generate_content(
            system + "\n\nConversation:\n" + history + "\n\nAssistant:",
            generation_config={"temperature": 0.4}
        )
        try:
            text = (resp.text or "").strip()
        except (ValueError, AttributeError):
            # gemini-2.5-pro returns thinking parts alongside the text part;
            # resp.text raises ValueError when multiple parts exist.
            parts = resp.candidates[0].content.parts
            text_parts = [p.text for p in parts if getattr(p, "text", None)]
            text = (text_parts[-1] if text_parts else "").strip()
        is_ready = text.startswith("READY:")
        compiled = text[6:].strip() if is_ready else None
        return jsonify({"reply": text if not is_ready else None, "compiled": compiled, "ready": is_ready})
    except Exception as e:
        return _json_error(str(e), 500, "community_chat_failed")


@app.route("/community/notes", methods=["POST"])
def community_add_note():
    result = _require_api_key_or_user()
    if result:
        return result
    if g.get("current_user_row"):
        csrf_error = _enforce_csrf()
        if csrf_error:
            return csrf_error
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return _json_error("content is required", 400, "missing_content")
    if len(content) > Config.MAX_COMMUNITY_NOTE_LENGTH:
        return _json_error(
            f"Content is too long (max {Config.MAX_COMMUNITY_NOTE_LENGTH} characters).",
            400,
            "content_too_long",
        )
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            INSERT INTO admin_knowledge (content, category, added_by, active)
            VALUES (%s, %s, %s, FALSE)
        """, (
            content,
            data.get("category", "community"),
            g.current_user_row["username"] if g.get("current_user_row") else "anonymous",
        ))
        conn.commit()
        return jsonify({"message": "Note submitted for review"}), 201
    except Exception as e:
        return _json_error(str(e), 500, "community_note_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/knowledge/pending", methods=["GET"])
def admin_get_pending():
    result = _require_admin()
    if result:
        return result
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, content, category, added_by, created_at
            FROM admin_knowledge
            WHERE active = FALSE
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        for row in rows:
            if row.get("created_at") and hasattr(row["created_at"], "isoformat"):
                row["created_at"] = row["created_at"].isoformat()
        return jsonify({"pending": rows})
    except Exception as e:
        return _json_error(str(e), 500, "pending_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route("/admin/knowledge/<int:entry_id>/approve", methods=["PUT"])
def admin_approve_note(entry_id):
    result = _require_admin_mutation()
    if result:
        return result
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("UPDATE admin_knowledge SET active = TRUE WHERE id = %s", (entry_id,))
        conn.commit()
        _trigger_ingest()
        return jsonify({"message": "Note approved"})
    except Exception as e:
        return _json_error(str(e), 500, "approve_failed")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


if __name__ == "__main__":
    print(f"\n🚀 Agent API {Config.API_VERSION}")
    print(f"   Host: {Config.HOST}:{Config.PORT}")
    print(f"   Auth Modes: user sessions{' + API keys' if Config.RETHINKAI_API_KEYS else ''}")
    print(f"   Debug: {Config.DEBUG}")
    print("   NOTE: this is the Flask dev server. For production use gunicorn:")
    print("         ./start_api.sh   (or: gunicorn -c api/gunicorn_conf.py api_v2:app)")
    print()
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG, threaded=True)
