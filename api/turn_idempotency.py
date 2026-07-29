"""Stable logical turn IDs and request fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Mapping, Optional


CLIENT_TURN_ID_MAX_LENGTH = 64
CLIENT_TURN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class InvalidClientTurnId(ValueError):
    pass


class FingerprintConflict(ValueError):
    pass


def validate_or_generate_client_turn_id(raw: Any) -> tuple[str, bool]:
    """Return (ID, client_supplied). Missing IDs remain backward compatible."""
    if raw is None or not str(raw).strip():
        return str(uuid.uuid4()), False
    value = str(raw).strip()
    if len(value) > CLIENT_TURN_ID_MAX_LENGTH or not CLIENT_TURN_ID_PATTERN.fullmatch(value):
        raise InvalidClientTurnId("client_turn_id is invalid")
    return value, True


def _bounded_context(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "entity_type",
        "entity_id",
        "entity_title",
        "entity_date",
        "event_id",
        "source",
    ):
        value = raw.get(key)
        if value is not None:
            result[key] = str(value).strip()[:256]
    return result


def build_request_fingerprint(
    *,
    owner_id: str,
    thread_id: str,
    message: str,
    ui_context: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> str:
    normalized_message = " ".join((message or "").strip().split())
    safe_options = {
        str(key)[:64]: value
        for key, value in sorted((options or {}).items())
        if isinstance(value, (str, int, float, bool, type(None)))
    }
    canonical = {
        "owner_id": str(owner_id),
        "thread_id": str(thread_id),
        "message": normalized_message,
        "ui_context": _bounded_context(ui_context),
        "options": safe_options,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
