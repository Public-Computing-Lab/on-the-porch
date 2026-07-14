#!/usr/bin/env python3
"""Behavior tests for deterministic, humanized event-detail answers."""

from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "on_the_porch"))

from event_context import (  # noqa: E402
    EventResolution,
    ValidatedEventContext,
    build_selected_event_response,
)
from sql_chat import app4, prepared_sql  # noqa: E402


EVENT_435_URL = "https://www.codman.org/event/virtual-baby-cafe-4/2026-07-15/"
EVENT_435 = {
    "id": 435,
    "source_pdf": "codman",
    "event_name": "Virtual Baby Cafe",
    "event_date": "2026-07-15",
    "start_date": date(2026, 7, 15),
    "end_date": date(2026, 7, 15),
    "start_time": time(14, 0),
    "end_time": time(16, 0),
    "raw_text": (
        "Virtual Baby Cafe\n"
        "Codman Square Health Center Baby Café Virtual \u200b \u200b "
        "Register here (857)-301-8259 2:00pm – 4:00pm\n"
        "Location: Virtual\n"
        f"URL: {EVENT_435_URL}"
    ),
    "category": "Youth/Family",
}


def _answer(row: dict, question: str = "Tell me more about this event") -> str:
    return prepared_sql.format_event_detail_answer(question, {"rows": [row]})


def _assert_no_invisible(value: str) -> None:
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        assert char not in value, repr(value)


def test_event_435_is_coherent_and_deduplicated() -> None:
    answer = _answer(EVENT_435)
    assert answer == (
        "**Virtual Baby Cafe** is scheduled for **Wednesday, July 15**, from "
        "**2:00 PM to 4:00 PM**. It is a virtual session hosted by Codman Square "
        "Health Center, so participants can attend remotely. Anyone interested in "
        "joining or confirming registration details can contact the organizer at "
        "(857) 301-8259.\n\nMore information is available on the "
        f"[event page]({EVENT_435_URL})."
    )
    assert answer.count("Virtual Baby Cafe") == 1
    assert answer.count("Wednesday, July 15") == 1
    assert answer.count("2:00 PM") == 1
    assert answer.count("4:00 PM") == 1
    assert answer.count("Codman Square Health Center") == 1
    assert answer.count("(857) 301-8259") == 1
    assert answer.count(EVENT_435_URL) == 1
    assert "2:00pm" not in answer
    assert "Register here" not in answer
    assert "Baby Café" not in answer
    _assert_no_invisible(answer)


def test_fully_populated_in_person_event_reads_naturally() -> None:
    url = "https://official.example.org/meeting"
    row = {
        "id": 900,
        "event_name": "Codman Square Community Meeting",
        "start_date": date(2026, 7, 16),
        "start_time": time(18, 0),
        "end_time": time(19, 30),
        "source_pdf": "codman",
        "raw_text": (
            "Codman Square Community Meeting\n"
            "The meeting will cover neighborhood updates and provide time for residents "
            "to ask questions.\n"
            "Location: Codman Square Library\n"
            "Contact: 857-301-8259\n"
            "Cost: Free\n"
            "Registration: Required\n"
            f"URL: {url}"
        ),
    }
    answer = _answer(row)
    assert "Thursday, July 16" in answer
    assert "6:00 PM to 7:30 PM" in answer
    assert "It will be held at Codman Square Library." in answer
    assert "The meeting will cover neighborhood updates" in answer
    assert "(857) 301-8259" in answer
    assert "event is free" in answer
    assert answer.count(url) == 1
    assert "**Location:**" not in answer
    assert "**Contact:**" not in answer


def test_virtual_event_without_phone_does_not_invent_contact() -> None:
    row = {
        "event_name": "Virtual Tenant Workshop",
        "start_date": date(2026, 7, 20),
        "start_time": time(18, 0),
        "source_pdf": "community calendar",
        "raw_text": (
            "Virtual Tenant Workshop\n"
            "Residents can learn about the annual recertification process.\n"
            "Location: Virtual"
        ),
    }
    answer = _answer(row)
    assert "virtual session" in answer
    assert "Residents can learn about" in answer
    assert "contact" not in answer.lower()
    assert not prepared_sql._event_phones(answer)


def test_phone_without_registration_cue_is_contact_only() -> None:
    row = {
        "event_name": "Neighborhood Office Hours",
        "start_date": date(2026, 7, 21),
        "source_pdf": "community calendar",
        "raw_text": (
            "Neighborhood Office Hours\n"
            "Residents can ask questions about city services. For questions, "
            "857.301.8259.\nLocation: Fields Corner Library"
        ),
    }
    answer = _answer(row)
    assert "For more information, call (857) 301-8259." in answer
    assert "registration details" not in answer
    assert "For questions" not in answer


def test_title_date_only_event_is_concise() -> None:
    row = {
        "id": 417,
        "event_name": "CREW & CADET STREET PARADE",
        "event_date": "Monday, July 13",
        "start_date": date(2026, 7, 13),
        "raw_text": "CREW & CADET STREET PARADE\nMonday, July 13",
        "source_pdf": "REP-25_26web.pdf",
    }
    assert _answer(row) == (
        "**CREW & CADET STREET PARADE** is scheduled for **Monday, July 13**. "
        "The current listing does not provide a time, a detailed description, or a "
        "separate location."
    )


def test_duplicate_metadata_is_removed_but_prose_is_preserved() -> None:
    row = {
        **EVENT_435,
        "event_name": "Community Skills Workshop",
        "start_date": date(2026, 7, 18),
        "start_time": time(10, 0),
        "end_time": time(11, 30),
        "raw_text": (
            "Community Skills Workshop\n"
            "Codman Square Health Center Community Skills Workshop 10:00am – 11:30am\n"
            "Residents will practice emergency-preparedness skills together.\n"
            "Location: Great Hall\n"
            "URL: https://official.example.org/skills"
        ),
    }
    answer = _answer(row)
    assert answer.count("Community Skills Workshop") == 1
    assert answer.count("Codman Square Health Center") == 0
    assert answer.count("10:00 AM") == 1
    assert answer.count("11:30 AM") == 1
    assert "Residents will practice emergency-preparedness skills together." in answer


def test_phone_variants_are_normalized() -> None:
    variants = (
        "857-301-8259",
        "(857)-301-8259",
        "(857) 301-8259",
        "857.301.8259",
    )
    for variant in variants:
        row = {
            "event_name": "Registration Clinic",
            "start_date": date(2026, 7, 18),
            "raw_text": (
                "Registration Clinic\nRegister here " + variant + "\nLocation: Great Hall"
            ),
        }
        answer = _answer(row)
        assert answer.count("(857) 301-8259") == 1, answer
        assert variant not in answer or variant == "(857) 301-8259"


def test_invisible_unicode_and_url_are_cleaned_once() -> None:
    url = "https://official.example.org/cleanup"
    row = {
        "event_name": "Community Cleanup",
        "start_date": date(2026, 7, 18),
        "raw_text": (
            "Community\u200b Cleanup\nHelp\u200c clean neighborhood streets. "
            f"{url}\nLocation: Codman Square\nURL: {url}\ufeff"
        ),
    }
    answer = _answer(row)
    _assert_no_invisible(answer)
    assert "Help clean neighborhood streets." in answer
    assert answer.count(url) == 1


def test_incidental_virtual_word_does_not_set_modality() -> None:
    row = {
        "event_name": "Digital Skills Workshop",
        "start_date": date(2026, 7, 18),
        "raw_text": (
            "Digital Skills Workshop\n"
            "Residents will practice using virtual tools for job applications."
        ),
    }
    answer = _answer(row)
    assert "using virtual tools" in answer
    assert "It is a virtual session" not in answer
    assert "separate location field" in answer

    explicit = {
        "event_name": "Summer Wellness Virtual Therapy Group",
        "start_date": date(2026, 7, 20),
        "raw_text": (
            "Summer Wellness Virtual Therapy Group\n"
            "This telehealth group helps students practice healthy coping skills."
        ),
    }
    explicit_answer = _answer(explicit)
    assert "It is a virtual session" in explicit_answer
    assert "separate location field" not in explicit_answer


def test_event_list_answer_is_unchanged() -> None:
    question = "What events are happening this weekend?"
    hit = prepared_sql.match_prepared_sql(question)
    assert hit is not None and hit.intent == "events_weekend"
    result = {
        "columns": ["event_name", "event_date", "start_date", "start_time"],
        "rows": [{
            "event_name": "Neighborhood Cleanup",
            "event_date": "Sat, Jul 18",
            "start_date": date(2026, 7, 18),
            "start_time": "10:00:00",
        }],
    }
    assert hit.answer_fn(question, result) == app4._format_events_answer_fallback(question, result)


def test_typed_and_selected_paths_use_the_same_answer() -> None:
    question = "Tell me more about the 'Virtual Baby Cafe' event happening on Jul 15."
    typed = prepared_sql.format_event_detail_answer(question, {"rows": [EVENT_435]})
    resolution = EventResolution(
        status="exact_id",
        context=ValidatedEventContext(
            entity_id="435",
            entity_title="Virtual Baby Cafe",
            entity_date="2026-07-15",
        ),
        event=EVENT_435,
    )
    selected = build_selected_event_response(question, resolution)
    assert selected.answer == typed
    assert selected.mode == "sql"
    assert selected.sources[0]["entity_ref"] == "event:435"
    assert selected.sources[0]["path"] == EVENT_435_URL


def main() -> int:
    tests = [
        test_event_435_is_coherent_and_deduplicated,
        test_fully_populated_in_person_event_reads_naturally,
        test_virtual_event_without_phone_does_not_invent_contact,
        test_phone_without_registration_cue_is_contact_only,
        test_title_date_only_event_is_concise,
        test_duplicate_metadata_is_removed_but_prose_is_preserved,
        test_phone_variants_are_normalized,
        test_invisible_unicode_and_url_are_cleaned_once,
        test_incidental_virtual_word_does_not_set_modality,
        test_event_list_answer_is_unchanged,
        test_typed_and_selected_paths_use_the_same_answer,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"OK   {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
