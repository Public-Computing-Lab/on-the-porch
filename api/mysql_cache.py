"""MySQL-backed shared cache.

Used when CACHE_BACKEND=mysql. Stores opaque string values with TTL in
api_cache_entries (see migrations/009_api_cache_entries.sql).

This is the only supported shared cache backend. Redis is not supported.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple


class MySQLCacheStore:
    """Small key/value cache with TTL, backed by MySQL."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def ping(self) -> bool:
        conn = None
        try:
            conn = self._connection_factory()
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return True
            finally:
                cursor.close()
        except Exception:
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def get(self, key: str) -> Optional[str]:
        conn = None
        try:
            conn = self._connection_factory()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT cache_value
                    FROM api_cache_entries
                    WHERE cache_key = %s AND expires_at > UTC_TIMESTAMP(6)
                    """,
                    (key,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return row[0] if isinstance(row[0], str) else str(row[0])
            finally:
                cursor.close()
        except Exception:
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        ttl_seconds = max(1, int(ttl_seconds))
        conn = None
        try:
            conn = self._connection_factory()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO api_cache_entries (cache_key, cache_value, expires_at)
                    VALUES (%s, %s, DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND))
                    ON DUPLICATE KEY UPDATE
                      cache_value = VALUES(cache_value),
                      expires_at = VALUES(expires_at)
                    """,
                    (key, value, ttl_seconds),
                )
                conn.commit()
                return True
            finally:
                cursor.close()
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> Tuple[bool, int]:
        """Fixed-window counter. Returns (allowed, retry_after_seconds)."""
        if limit <= 0 or window_seconds <= 0:
            return True, 0

        conn = None
        try:
            conn = self._connection_factory()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO api_cache_entries (cache_key, cache_value, expires_at)
                    VALUES (%s, '1', DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND))
                    ON DUPLICATE KEY UPDATE
                      cache_value = IF(
                        expires_at <= UTC_TIMESTAMP(6),
                        '1',
                        CAST(cache_value AS UNSIGNED) + 1
                      ),
                      expires_at = IF(
                        expires_at <= UTC_TIMESTAMP(6),
                        DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND),
                        expires_at
                      )
                    """,
                    (key, window_seconds, window_seconds),
                )
                cursor.execute(
                    """
                    SELECT
                      CAST(cache_value AS UNSIGNED),
                      GREATEST(1, TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(6), expires_at))
                    FROM api_cache_entries
                    WHERE cache_key = %s
                    """,
                    (key,),
                )
                row = cursor.fetchone()
                conn.commit()
                if not row:
                    return True, 0
                count = int(row[0] or 0)
                retry_after = int(row[1] or window_seconds)
                if count > limit:
                    return False, retry_after
                return True, 0
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cursor.close()
        except Exception:
            return True, 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
