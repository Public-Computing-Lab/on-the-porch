"""Lightweight request rate limiting with MySQL or explicit in-process mode.

Redis is not supported. Shared multi-worker limits use MySQL via MySQLCacheStore.
Process-local memory is only allowed when the caller explicitly opts into
single-process development mode (never as a silent MySQL fallback).
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple


class RateLimitBackendError(RuntimeError):
    """MySQL rate-limit backend failed; caller should map to a controlled response."""


class RateLimiter:
    """Fixed-window counter. MySQL when configured; thread-safe memory otherwise."""

    def __init__(
        self,
        *,
        mysql_store=None,
        key_prefix: str = "rethinkai:ratelimit:",
        require_mysql: bool = False,
    ) -> None:
        self._mysql = mysql_store
        self._prefix = key_prefix
        self._require_mysql = bool(require_mysql or mysql_store is not None)
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _key(self, bucket: str, identifier: str) -> str:
        return f"{self._prefix}{bucket}:{identifier}"

    def check(self, bucket: str, identifier: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds).

        When MySQL is the configured shared backend, errors propagate as
        RateLimitBackendError (fail closed) — never silently allow traffic.
        """
        if limit <= 0 or window_seconds <= 0:
            return True, 0

        key = self._key(bucket, identifier)
        if self._mysql is not None:
            try:
                return self._mysql.check_rate_limit(key, limit, window_seconds)
            except Exception as exc:  # noqa: BLE001
                raise RateLimitBackendError(
                    "shared rate-limit backend unavailable"
                ) from exc
        if self._require_mysql:
            raise RateLimitBackendError("shared rate-limit backend unavailable")

        now = time.time()
        with self._lock:
            count, window_start = self._memory.get(key, (0, now))
            if now - window_start >= window_seconds:
                count = 0
                window_start = now
            count += 1
            self._memory[key] = (count, window_start)
            retry_after = max(1, int(window_seconds - (now - window_start)))
            if count > limit:
                return False, retry_after
        return True, 0
