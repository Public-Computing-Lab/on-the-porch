from __future__ import annotations

import sys
from pathlib import Path

import pytest


API = Path(__file__).resolve().parents[1] / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from turn_idempotency import (  # noqa: E402
    InvalidClientTurnId,
    build_request_fingerprint,
    validate_or_generate_client_turn_id,
)


def test_valid_client_id_is_preserved() -> None:
    value, supplied = validate_or_generate_client_turn_id(
        "0d2c8493-f68a-48ca-83da-85970b178e4d"
    )
    assert value == "0d2c8493-f68a-48ca-83da-85970b178e4d"
    assert supplied is True


def test_missing_client_id_is_backward_compatible() -> None:
    value, supplied = validate_or_generate_client_turn_id(None)
    assert len(value) == 36
    assert supplied is False


@pytest.mark.parametrize("value", ["short", "bad id!!", "x" * 65])
def test_invalid_client_ids_are_rejected(value: str) -> None:
    with pytest.raises(InvalidClientTurnId):
        validate_or_generate_client_turn_id(value)


def test_fingerprint_is_stable_and_owner_scoped() -> None:
    kwargs = {
        "owner_id": "owner-a",
        "thread_id": "thread-a",
        "message": "  Where   is it? ",
        "ui_context": {"entity_type": "event", "entity_id": "435"},
        "options": {"response_format": "chat"},
    }
    first = build_request_fingerprint(**kwargs)
    second = build_request_fingerprint(**kwargs)
    assert first == second
    assert first != build_request_fingerprint(**{**kwargs, "owner_id": "owner-b"})
    assert first != build_request_fingerprint(**{**kwargs, "thread_id": "thread-b"})
    assert first != build_request_fingerprint(**{**kwargs, "message": "When is it?"})
    assert first != build_request_fingerprint(
        **{
            **kwargs,
            "ui_context": {"entity_type": "event", "entity_id": "436"},
        }
    )


def test_fingerprint_does_not_include_unapproved_context() -> None:
    common = {
        "owner_id": "owner",
        "thread_id": "thread",
        "message": "Tell me more",
    }
    first = build_request_fingerprint(
        **common,
        ui_context={"entity_id": "435", "secret": "one"},
    )
    second = build_request_fingerprint(
        **common,
        ui_context={"entity_id": "435", "secret": "two"},
    )
    assert first == second
