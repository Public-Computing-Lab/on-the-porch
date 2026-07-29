from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_routes_share_conversation_turn_service() -> None:
    source = (ROOT / "api" / "api_v2.py").read_text(encoding="utf-8")
    assert source.count("_conversation_turn_service.execute(") == 2
    assert source.count("def _turn_producer(") == 1
    assert source.count("_turn_producer,") == 2
    assert "_json_turn_producer" not in source
    assert "_stream_turn_producer" not in source
    assert source.count("_finalize_conversation_turn(") == 1


def test_frontend_reuses_turn_payload_for_stream_and_fallback() -> None:
    source = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "const turnPayload =" in source
    assert "sendMessageStream(threadId, turnPayload" in source
    assert source.count("sendMessage(threadId, turnPayload)") >= 3
    assert "waitForTurn(threadId, clientTurnId)" in source
