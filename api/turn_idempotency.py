"""MySQL-backed conversation turn idempotency (client_turn_id).

Short transactions only. Do not hold connections during LLM/RAG/SQL generation.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

UTC = timezone.utc

CLIENT_TURN_ID_MAX_LEN = 64
CLIENT_TURN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
LEASE_SECONDS = 120
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
ALLOWED_STATUSES = frozenset(
    {STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_FAILED}
)


class TurnIdempotencyError(ValueError):
    """Invalid client_turn_id or fingerprint inputs."""


class IdempotencyKeyReused(Exception):
    """Same client_turn_id with a different request fingerprint."""


@dataclass(frozen=True)
class TurnReservation:
    turn_row_id: str
    client_turn_id: str
    request_fingerprint: str
    lease_owner: str
    status: str
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    replay: bool = False
    owned: bool = True


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_naive_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    return None


def validate_client_turn_id(raw: Any) -> Optional[str]:
    """Return normalized ID, None if absent, or raise TurnIdempotencyError."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > CLIENT_TURN_ID_MAX_LEN:
        raise TurnIdempotencyError("client_turn_id is too long")
    if not CLIENT_TURN_ID_RE.match(text):
        raise TurnIdempotencyError("client_turn_id is invalid")
    return text


def build_request_fingerprint(
    *,
    thread_id: str,
    message: str,
    event_context: Optional[Mapping[str, Any]] = None,
) -> str:
    """Deterministic fingerprint from authoritative request inputs only."""
    normalized_message = " ".join((message or "").strip().split())
    event_payload: Dict[str, Any] = {}
    if isinstance(event_context, Mapping):
        # Only stable identifiers from validated UI context.
        for key in (
            "event_id",
            "id",
            "selected_event_id",
            "entity_id",
            "submitted_entity_id",
        ):
            if event_context.get(key) is not None:
                event_payload[key] = str(event_context.get(key))
        title = event_context.get("title")
        if title is not None:
            event_payload["title"] = str(title).strip()[:200]
        date_val = event_context.get("date") or event_context.get("event_date")
        if date_val is not None:
            event_payload["date"] = str(date_val)[:32]
    canonical = {
        "thread_id": str(thread_id),
        "message": normalized_message,
        "event_context": event_payload,
    }
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row_to_reservation(
    row: Mapping[str, Any],
    *,
    replay: bool = False,
    owned: bool = True,
) -> TurnReservation:
    return TurnReservation(
        turn_row_id=str(row["id"]),
        client_turn_id=str(row["client_turn_id"]),
        request_fingerprint=str(row["request_fingerprint"]),
        lease_owner=str(row["lease_owner"] or ""),
        status=str(row["status"]),
        user_message_id=row.get("user_message_id"),
        assistant_message_id=row.get("assistant_message_id"),
        replay=replay,
        owned=owned,
    )


def load_turn(
    conn,
    *,
    thread_id: str,
    client_turn_id: str,
) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, thread_id, user_id, client_turn_id, request_fingerprint, status,
                   lease_owner, lease_expires_at, user_message_id, assistant_message_id,
                   created_at, updated_at, completed_at
            FROM conversation_turns
            WHERE thread_id = %s AND client_turn_id = %s
            LIMIT 1
            """,
            (thread_id, client_turn_id),
        )
        return cursor.fetchone()
    finally:
        cursor.close()


def begin_or_replay_turn(
    connection_factory: Callable[[], Any],
    *,
    thread_id: str,
    user_id: str,
    client_turn_id: str,
    request_fingerprint: str,
    lease_seconds: int = LEASE_SECONDS,
) -> TurnReservation:
    """Reserve a turn or return a completed replay. Short MySQL transactions only."""
    lease_owner = str(uuid.uuid4())
    now = utcnow()
    expires = now + timedelta(seconds=max(15, int(lease_seconds)))
    turn_id = str(uuid.uuid4())
    conn = connection_factory()
    try:
        conn.start_transaction()
        existing = load_turn(conn, thread_id=thread_id, client_turn_id=client_turn_id)
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                conn.rollback()
                raise IdempotencyKeyReused("client_turn_id reused with different payload")
            status = str(existing["status"])
            if status == STATUS_COMPLETED and existing.get("user_message_id") and existing.get(
                "assistant_message_id"
            ):
                conn.commit()
                return _row_to_reservation(existing, replay=True)
            lease_exp = _as_naive_utc(existing.get("lease_expires_at"))
            lease_active = (
                status == STATUS_IN_PROGRESS
                and lease_exp is not None
                and lease_exp > now
                and existing.get("lease_owner")
                and existing.get("lease_owner") != lease_owner
            )
            if lease_active:
                # Another worker holds a live lease — wait path handled by caller via replay poll.
                conn.commit()
                return _row_to_reservation(existing, replay=False, owned=False)
            # Reclaim FAILED / expired IN_PROGRESS for the same fingerprint.
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE conversation_turns
                    SET status = %s,
                        lease_owner = %s,
                        lease_expires_at = %s,
                        user_message_id = NULL,
                        assistant_message_id = NULL,
                        completed_at = NULL,
                        updated_at = %s
                    WHERE id = %s
                      AND request_fingerprint = %s
                      AND (
                        status = %s
                        OR lease_expires_at IS NULL
                        OR lease_expires_at <= %s
                      )
                    """,
                    (
                        STATUS_IN_PROGRESS,
                        lease_owner,
                        expires,
                        now,
                        existing["id"],
                        request_fingerprint,
                        STATUS_FAILED,
                        now,
                    ),
                )
                if cursor.rowcount < 1:
                    conn.rollback()
                    refreshed = load_turn(
                        conn,
                        thread_id=thread_id,
                        client_turn_id=client_turn_id,
                    )
                    if refreshed and refreshed.get("status") == STATUS_COMPLETED:
                        return _row_to_reservation(refreshed, replay=True)
                    if refreshed is not None:
                        return _row_to_reservation(refreshed, replay=False, owned=False)
                    raise TurnIdempotencyError("client_turn_id reservation lost")
                conn.commit()
                existing["status"] = STATUS_IN_PROGRESS
                existing["lease_owner"] = lease_owner
                existing["lease_expires_at"] = expires
                existing["user_message_id"] = None
                existing["assistant_message_id"] = None
                return _row_to_reservation(existing, replay=False, owned=True)
            finally:
                cursor.close()

        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO conversation_turns (
                    id, thread_id, user_id, client_turn_id, request_fingerprint,
                    status, lease_owner, lease_expires_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    turn_id,
                    thread_id,
                    user_id,
                    client_turn_id,
                    request_fingerprint,
                    STATUS_IN_PROGRESS,
                    lease_owner,
                    expires,
                    now,
                    now,
                ),
            )
            conn.commit()
            return TurnReservation(
                turn_row_id=turn_id,
                client_turn_id=client_turn_id,
                request_fingerprint=request_fingerprint,
                lease_owner=lease_owner,
                status=STATUS_IN_PROGRESS,
                replay=False,
                owned=True,
            )
        except Exception as exc:
            conn.rollback()
            # Duplicate insert race — load winner.
            msg = str(exc).lower()
            if "duplicate" not in msg and getattr(exc, "errno", None) not in {1062, 1586}:
                raise
            winner = load_turn(
                connection_factory(),
                thread_id=thread_id,
                client_turn_id=client_turn_id,
            )
            if winner is None:
                raise
            if winner["request_fingerprint"] != request_fingerprint:
                raise IdempotencyKeyReused("client_turn_id reused with different payload")
            if winner.get("status") == STATUS_COMPLETED:
                return _row_to_reservation(winner, replay=True)
            owned = str(winner.get("lease_owner") or "") == lease_owner
            return _row_to_reservation(winner, replay=False, owned=owned)
        finally:
            cursor.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def complete_turn_on_connection(
    conn,
    *,
    turn_row_id: str,
    lease_owner: str,
    user_message_id: str,
    assistant_message_id: str,
) -> bool:
    """Mark COMPLETED on an open connection (caller owns commit/rollback)."""
    cursor = conn.cursor()
    try:
        now = utcnow()
        cursor.execute(
            """
            UPDATE conversation_turns
            SET status = %s,
                user_message_id = %s,
                assistant_message_id = %s,
                lease_owner = NULL,
                lease_expires_at = NULL,
                completed_at = %s,
                updated_at = %s
            WHERE id = %s
              AND status = %s
              AND (lease_owner = %s OR lease_owner IS NULL)
            """,
            (
                STATUS_COMPLETED,
                user_message_id,
                assistant_message_id,
                now,
                now,
                turn_row_id,
                STATUS_IN_PROGRESS,
                lease_owner,
            ),
        )
        return cursor.rowcount > 0
    finally:
        cursor.close()


def complete_turn(
    connection_factory: Callable[[], Any],
    *,
    turn_row_id: str,
    lease_owner: str,
    user_message_id: str,
    assistant_message_id: str,
) -> bool:
    conn = connection_factory()
    try:
        try:
            ok = complete_turn_on_connection(
                conn,
                turn_row_id=turn_row_id,
                lease_owner=lease_owner,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            conn.commit()
            return ok
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fail_turn(
    connection_factory: Callable[[], Any],
    *,
    turn_row_id: str,
    lease_owner: str,
) -> bool:
    """Mark a reservation FAILED so the same client_turn_id can be retried."""
    conn = connection_factory()
    try:
        cursor = conn.cursor()
        try:
            now = utcnow()
            cursor.execute(
                """
                UPDATE conversation_turns
                SET status = %s,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = %s
                WHERE id = %s
                  AND status = %s
                  AND (lease_owner = %s OR lease_owner IS NULL)
                """,
                (
                    STATUS_FAILED,
                    now,
                    turn_row_id,
                    STATUS_IN_PROGRESS,
                    lease_owner,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def wait_for_completed_turn(
    connection_factory: Callable[[], Any],
    *,
    thread_id: str,
    client_turn_id: str,
    request_fingerprint: str,
    timeout_seconds: float = 3.0,
    poll_interval_seconds: float = 0.05,
) -> Optional[TurnReservation]:
    """Poll briefly for a concurrent winner to finish; return replay reservation."""
    import time

    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while time.monotonic() < deadline:
        row = None
        conn = None
        try:
            conn = connection_factory()
            row = load_turn(conn, thread_id=thread_id, client_turn_id=client_turn_id)
        except Exception:
            row = None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        if row is None:
            time.sleep(poll_interval_seconds)
            continue
        if row.get("request_fingerprint") != request_fingerprint:
            raise IdempotencyKeyReused("client_turn_id reused with different payload")
        if (
            row.get("status") == STATUS_COMPLETED
            and row.get("user_message_id")
            and row.get("assistant_message_id")
        ):
            return _row_to_reservation(row, replay=True)
        if row.get("status") == STATUS_FAILED:
            return None
        time.sleep(poll_interval_seconds)
    return None
