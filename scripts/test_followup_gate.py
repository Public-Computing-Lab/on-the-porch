#!/usr/bin/env python3
"""Tests for follow-up gate topic mismatch rules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from unified_chatbot import (  # noqa: E402
    _fast_followup_needs_new_data,
    resolve_effective_question,
)


def _311_cache() -> dict:
    return {
        "mode": "sql",
        "sql_query": (
            "SELECT COUNT(*) AS cnt FROM `service_requests_311` "
            "WHERE (`neighborhood` = 'Dorchester' OR `police_district` = 'C11')"
        ),
        "sql_result": {"rows": [{"cnt": 3183}]},
        "answer": "There were 3,183 311 requests last month.",
    }


def test_events_after_311_needs_fresh_data():
    cache = _311_cache()
    history = [
        {"role": "user", "content": "How many 311 and 911 last month?"},
        {"role": "assistant", "content": cache["answer"]},
    ]
    result = _fast_followup_needs_new_data(
        "What events are happening this week?",
        history,
        cache,
    )
    assert result is not None
    assert result["needs_new_data"] is True, result


def test_referential_311_followup_uses_cache():
    cache = _311_cache()
    history = [
        {"role": "user", "content": "How many 311 last month?"},
        {"role": "assistant", "content": cache["answer"]},
    ]
    result = _fast_followup_needs_new_data(
        "tell me more about that",
        history,
        cache,
    )
    assert result is not None
    assert result["needs_new_data"] is False, result


def test_311_expansion_needs_fresh_data():
    cache = _311_cache()
    history = [
        {"role": "user", "content": "How many 311 requests last month?"},
        {"role": "assistant", "content": cache["answer"]},
    ]
    result = _fast_followup_needs_new_data(
        "tell me more about the 311 requests",
        history,
        cache,
    )
    assert result is not None
    assert result["needs_new_data"] is True, result


def test_pothole_recent_needs_fresh_data():
    cache = _311_cache()
    result = _fast_followup_needs_new_data(
        "any recent pothole reports?",
        [{"role": "user", "content": "311 last month"}],
        cache,
    )
    assert result is not None
    assert result["needs_new_data"] is True, result


def test_rewrite_311_expansion():
    cache = _311_cache()
    cache["question"] = "How many 311 requests were filed last month?"
    rewritten, force = resolve_effective_question(
        "tell me more about the 311 requests",
        [],
        cache,
    )
    assert force is True
    assert "top 311 issue types" in rewritten.lower()
    assert "last month" in rewritten.lower()


def test_trim_repetitive_opener_on_followup():
    from answer_voice import trim_repetitive_opener  # noqa: WPS433

    history = [
        {"role": "user", "content": "311 count?"},
        {"role": "assistant", "content": "There were 2,722 requests."},
    ]
    raw = "Hey there! Glad you stopped by. This week there are several events."
    out = trim_repetitive_opener(raw, history)
    assert "hey there" not in out.lower()
    assert out.startswith("This week")


def test_scrub_false_911_zero_claim():
    from answer_voice import fix_unavailable_data_claims  # noqa: WPS433

    blocks = [{
        "intent": "911_unavailable",
        "topic": "911",
        "data_unavailable": True,
        "canonical_facts": (
            "We don't have Boston **911 emergency dispatch call** data in this app."
        ),
    }]
    raw = (
        "There were **2,722** 311 requests. "
        "On the 911 side, there have been **0** emergency calls."
    )
    out = fix_unavailable_data_claims(raw, blocks)
    assert "0" not in out or "don't have" in out.lower()
    assert "don't have" in out.lower()


def test_no_duplicate_911_disclosure_when_llm_already_explained():
    from answer_voice import fix_unavailable_data_claims  # noqa: WPS433

    blocks = [{
        "intent": "911_unavailable",
        "topic": "911",
        "data_unavailable": True,
        "canonical_facts": (
            "We don't have Boston **911 emergency dispatch call** data in this app. "
            "911 call logs are separate from BPD crime incident reports and 311 service requests.\n\n"
            "For **crime statistics** in Dorchester, ask about crime incidents or shootings. "
            "For **non-emergency city services** (potholes, trash, lights), ask about **311 requests**."
        ),
    }]
    raw = (
        "For Dorchester last month there were **3,183** 311 requests. "
        "Regarding 911, this app doesn't have access to emergency dispatch call data — "
        "those logs are kept separately from 311 and BPD crime reports. "
        "You can ask about crime incidents or shootings for safety stats, or 311 for city services."
    )
    out = fix_unavailable_data_claims(raw, blocks)
    assert out.count("911 call logs are separate") == 0
    assert out == raw


def main() -> int:
    tests = [
        test_events_after_311_needs_fresh_data,
        test_referential_311_followup_uses_cache,
        test_311_expansion_needs_fresh_data,
        test_pothole_recent_needs_fresh_data,
        test_rewrite_311_expansion,
        test_trim_repetitive_opener_on_followup,
        test_scrub_false_911_zero_claim,
        test_no_duplicate_911_disclosure_when_llm_already_explained,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
