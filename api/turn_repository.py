"""MySQL repository for cross-worker conversation-turn coordination."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional


STATUS_RESERVED = "RESERVED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
ACTIVE_STATUSES = frozenset({STATUS_RESERVED, STATUS_RUNNING})
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})


class ReservationKind(str, Enum):
    NEW_EXECUTION_OWNER = "new_execution_owner"
    COMPLETED_REPLAY = "completed_replay"
    RUNNING_EXISTING = "running_existing"
    FAILED_REPLAY = "failed_replay"
    FINGERPRINT_CONFLICT = "fingerprint_conflict"
    THREAD_BUSY = "thread_busy"
    OWNERSHIP_FAILURE = "ownership_failure"


@dataclass(frozen=True)
class TurnReservation:
    kind: ReservationKind
    turn_id: Optional[str]
    client_turn_id: str
    request_fingerprint: str
    status: Optional[str]
    lease_owner: Optional[str] = None
    state_version: int = 0
    response_payload: Optional[dict[str, Any]] = None
    error_category: Optional[str] = None
    client_idempotent: bool = True

    @property
    def owns_execution(self) -> bool:
        return self.kind is ReservationKind.NEW_EXECUTION_OWNER


class TurnRepository:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        lease_seconds: int = 180,
    ) -> None:
        self.connection_factory = connection_factory
        self.lease_seconds = max(30, min(int(lease_seconds), 900))

    @staticmethod
    def _load_turn(
        conn: Any,
        *,
        thread_id: str,
        client_turn_id: str,
        user_id: Optional[str] = None,
        for_update: bool = False,
    ) -> Optional[dict[str, Any]]:
        cursor = conn.cursor(dictionary=True)
        try:
            where_owner = " AND user_id = %s" if user_id is not None else ""
            lock = " FOR UPDATE" if for_update else ""
            params: list[Any] = [thread_id, client_turn_id]
            if user_id is not None:
                params.append(user_id)
            cursor.execute(
                f"""
                SELECT *,
                       (lease_expires_at IS NOT NULL
                        AND lease_expires_at > UTC_TIMESTAMP(6)) AS lease_active
                FROM conversation_turns
                WHERE thread_id = %s AND client_turn_id = %s{where_owner}
                LIMIT 1{lock}
                """,
                tuple(params),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    @staticmethod
    def _payload(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        raw = row.get("response_payload_json")
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError):
            return None

    def reserve(
        self,
        *,
        thread_id: str,
        user_id: str,
        client_turn_id: str,
        request_fingerprint: str,
        transport_origin: str,
        client_idempotent: bool,
        trace_id: Optional[str],
    ) -> TurnReservation:
        conn = self.connection_factory()
        try:
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT id, user_id, active_turn_id, thread_state_version
                    FROM conversation_threads
                    WHERE id = %s AND user_id = %s AND deleted_at IS NULL
                    LIMIT 1 FOR UPDATE
                    """,
                    (thread_id, user_id),
                )
                thread = cursor.fetchone()
            finally:
                cursor.close()
            if thread is None:
                conn.rollback()
                return TurnReservation(
                    ReservationKind.OWNERSHIP_FAILURE,
                    None,
                    client_turn_id,
                    request_fingerprint,
                    None,
                    client_idempotent=client_idempotent,
                )

            existing = self._load_turn(
                conn,
                thread_id=thread_id,
                client_turn_id=client_turn_id,
                for_update=True,
            )
            if existing is not None:
                if existing.get("user_id") != user_id:
                    conn.rollback()
                    return TurnReservation(
                        ReservationKind.OWNERSHIP_FAILURE,
                        None,
                        client_turn_id,
                        request_fingerprint,
                        None,
                        client_idempotent=client_idempotent,
                    )
                if existing.get("request_fingerprint") != request_fingerprint:
                    conn.rollback()
                    return TurnReservation(
                        ReservationKind.FINGERPRINT_CONFLICT,
                        str(existing["id"]),
                        client_turn_id,
                        request_fingerprint,
                        str(existing.get("status") or ""),
                        client_idempotent=client_idempotent,
                    )
                status = str(existing.get("status") or "")
                if status == STATUS_COMPLETED:
                    conn.commit()
                    return TurnReservation(
                        ReservationKind.COMPLETED_REPLAY,
                        str(existing["id"]),
                        client_turn_id,
                        request_fingerprint,
                        status,
                        state_version=int(existing.get("state_version_at_start") or 0),
                        response_payload=self._payload(existing),
                        client_idempotent=client_idempotent,
                    )
                if status == STATUS_FAILED:
                    conn.commit()
                    return TurnReservation(
                        ReservationKind.FAILED_REPLAY,
                        str(existing["id"]),
                        client_turn_id,
                        request_fingerprint,
                        status,
                        error_category=existing.get("normalized_error_category"),
                        client_idempotent=client_idempotent,
                    )
                if status in ACTIVE_STATUSES and bool(existing.get("lease_active")):
                    conn.commit()
                    return TurnReservation(
                        ReservationKind.RUNNING_EXISTING,
                        str(existing["id"]),
                        client_turn_id,
                        request_fingerprint,
                        status,
                        client_idempotent=client_idempotent,
                    )
                # An abandoned same-ID turn becomes deterministic failure.
                self._mark_failed_on_connection(
                    conn,
                    turn_id=str(existing["id"]),
                    user_id=user_id,
                    error_category="abandoned_turn",
                    clear_active=True,
                )
                conn.commit()
                return TurnReservation(
                    ReservationKind.FAILED_REPLAY,
                    str(existing["id"]),
                    client_turn_id,
                    request_fingerprint,
                    STATUS_FAILED,
                    error_category="abandoned_turn",
                    client_idempotent=client_idempotent,
                )

            active_turn_id = thread.get("active_turn_id")
            if active_turn_id:
                cursor = conn.cursor(dictionary=True)
                try:
                    cursor.execute(
                        """
                        SELECT id, status,
                               (lease_expires_at IS NOT NULL
                                AND lease_expires_at > UTC_TIMESTAMP(6)) AS lease_active
                        FROM conversation_turns WHERE id = %s LIMIT 1 FOR UPDATE
                        """,
                        (active_turn_id,),
                    )
                    active = cursor.fetchone()
                finally:
                    cursor.close()
                if active and str(active.get("status")) in ACTIVE_STATUSES and bool(
                    active.get("lease_active")
                ):
                    conn.commit()
                    return TurnReservation(
                        ReservationKind.THREAD_BUSY,
                        str(active["id"]),
                        client_turn_id,
                        request_fingerprint,
                        str(active.get("status")),
                        client_idempotent=client_idempotent,
                    )
                cursor = conn.cursor()
                try:
                    cursor.execute(
                        """
                        UPDATE conversation_threads SET active_turn_id = NULL
                        WHERE id = %s AND active_turn_id = %s
                        """,
                        (thread_id, active_turn_id),
                    )
                finally:
                    cursor.close()

            turn_id = str(uuid.uuid4())
            lease_owner = str(uuid.uuid4())
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO conversation_turns (
                        id, thread_id, user_id, client_turn_id, request_fingerprint,
                        transport_origin, client_idempotent, status, lease_owner,
                        lease_expires_at, state_version_at_start,
                        observability_trace_id, created_at, reserved_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND), %s, %s,
                        UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), UTC_TIMESTAMP(6)
                    )
                    """,
                    (
                        turn_id,
                        thread_id,
                        user_id,
                        client_turn_id,
                        request_fingerprint,
                        transport_origin[:16],
                        bool(client_idempotent),
                        STATUS_RESERVED,
                        lease_owner,
                        self.lease_seconds,
                        int(thread.get("thread_state_version") or 0),
                        (trace_id or "")[:64] or None,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE conversation_threads SET active_turn_id = %s
                    WHERE id = %s AND user_id = %s AND active_turn_id IS NULL
                    """,
                    (turn_id, thread_id, user_id),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return TurnReservation(
                        ReservationKind.THREAD_BUSY,
                        None,
                        client_turn_id,
                        request_fingerprint,
                        None,
                        client_idempotent=client_idempotent,
                    )
                conn.commit()
                return TurnReservation(
                    ReservationKind.NEW_EXECUTION_OWNER,
                    turn_id,
                    client_turn_id,
                    request_fingerprint,
                    STATUS_RESERVED,
                    lease_owner=lease_owner,
                    state_version=int(thread.get("thread_state_version") or 0),
                    client_idempotent=client_idempotent,
                )
            finally:
                cursor.close()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_running(self, reservation: TurnReservation, *, user_id: str) -> bool:
        if not reservation.turn_id or not reservation.lease_owner:
            return False
        conn = self.connection_factory()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE conversation_turns
                    SET status = %s, started_at = UTC_TIMESTAMP(6),
                        updated_at = UTC_TIMESTAMP(6)
                    WHERE id = %s AND user_id = %s AND status = %s
                      AND lease_owner = %s
                    """,
                    (
                        STATUS_RUNNING,
                        reservation.turn_id,
                        user_id,
                        STATUS_RESERVED,
                        reservation.lease_owner,
                    ),
                )
                ok = cursor.rowcount == 1
                conn.commit() if ok else conn.rollback()
                return ok
            finally:
                cursor.close()
        finally:
            conn.close()

    @staticmethod
    def _mark_failed_on_connection(
        conn: Any,
        *,
        turn_id: str,
        user_id: str,
        error_category: str,
        clear_active: bool,
        lease_owner: Optional[str] = None,
    ) -> bool:
        cursor = conn.cursor()
        try:
            owner_clause = " AND lease_owner = %s" if lease_owner else ""
            params: list[Any] = [
                STATUS_FAILED,
                error_category[:80],
                turn_id,
                user_id,
                STATUS_RESERVED,
                STATUS_RUNNING,
            ]
            if lease_owner:
                params.append(lease_owner)
            cursor.execute(
                f"""
                UPDATE conversation_turns
                SET status = %s, normalized_error_category = %s,
                    lease_owner = NULL, lease_expires_at = NULL,
                    completed_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6)
                WHERE id = %s AND user_id = %s
                  AND status IN (%s, %s){owner_clause}
                """,
                tuple(params),
            )
            ok = cursor.rowcount == 1
            if ok and clear_active:
                cursor.execute(
                    """
                    UPDATE conversation_threads SET active_turn_id = NULL
                    WHERE active_turn_id = %s AND user_id = %s
                    """,
                    (turn_id, user_id),
                )
            return ok
        finally:
            cursor.close()

    def mark_failed(
        self,
        reservation: TurnReservation,
        *,
        user_id: str,
        error_category: str,
    ) -> bool:
        if not reservation.turn_id:
            return False
        conn = self.connection_factory()
        try:
            conn.start_transaction()
            ok = self._mark_failed_on_connection(
                conn,
                turn_id=reservation.turn_id,
                user_id=user_id,
                error_category=error_category,
                clear_active=True,
                lease_owner=reservation.lease_owner,
            )
            conn.commit() if ok else conn.rollback()
            return ok
        finally:
            conn.close()

    def complete_on_connection(
        self,
        conn: Any,
        *,
        reservation: TurnReservation,
        user_id: str,
        user_message_id: str,
        assistant_message_id: str,
        response_payload: Mapping[str, Any],
    ) -> bool:
        if not reservation.turn_id or not reservation.lease_owner:
            return False
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE conversation_turns
                SET status = %s, user_message_id = %s, assistant_message_id = %s,
                    response_payload_json = %s, normalized_error_category = NULL,
                    lease_owner = NULL, lease_expires_at = NULL,
                    completed_at = UTC_TIMESTAMP(6), updated_at = UTC_TIMESTAMP(6)
                WHERE id = %s AND user_id = %s AND status = %s
                  AND lease_owner = %s AND request_fingerprint = %s
                """,
                (
                    STATUS_COMPLETED,
                    user_message_id,
                    assistant_message_id,
                    json.dumps(response_payload, ensure_ascii=True, separators=(",", ":")),
                    reservation.turn_id,
                    user_id,
                    STATUS_RUNNING,
                    reservation.lease_owner,
                    reservation.request_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                """
                UPDATE conversation_threads SET active_turn_id = NULL
                WHERE active_turn_id = %s AND user_id = %s
                """,
                (reservation.turn_id, user_id),
            )
            return cursor.rowcount == 1
        finally:
            cursor.close()

    def load_owned(
        self,
        *,
        thread_id: str,
        user_id: str,
        client_turn_id: str,
    ) -> Optional[dict[str, Any]]:
        conn = self.connection_factory()
        try:
            return self._load_turn(
                conn,
                thread_id=thread_id,
                client_turn_id=client_turn_id,
                user_id=user_id,
            )
        finally:
            conn.close()
