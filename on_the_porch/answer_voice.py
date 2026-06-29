"""Shared conversational voice for every chat path (SQL, RAG, hybrid, history, chitchat).

One set of principles and per-question-type prompts so answers sound like a helpful
neighbor — not database exports, template dumps, or robotic lists.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Sequence

AnswerKind = Literal[
    "chitchat",
    "civic_count",
    "civic_breakdown",
    "civic_detail",
    "events",
    "crime",
    "guardrail",
    "rag_general",
    "rag_transcript",
    "hybrid",
    "multi_part",
    "history_followup",
]

# Intents that return row-level or grouped data — never echo as raw lists.
DETAIL_CIVIC_INTENTS = frozenset({
    "311_top_types",
    "311_recent",
    "311_issue_recent",
    "311_open",
    "events_list",
    "events_by_source",
    "crime_by_offense",
})

COUNT_CIVIC_INTENTS = frozenset({
    "311_weekly_count",
    "311_issue_count",
    "311_count_with_types",
    "crime_monthly_count",
    "crime_shooting_count",
    "crime_homicide_count",
})

EVENT_INTENTS = frozenset({"events_list", "events_by_source", "event_by_name"})

# Intents where we must explain missing data — never surface placeholder zeros as facts.
UNAVAILABLE_DATA_INTENTS = frozenset({"911_unavailable", "unavailable"})

CRIME_INTENTS = frozenset({
    "crime_monthly_count",
    "crime_shooting_count",
    "crime_homicide_count",
    "crime_by_offense",
})

CORE_VOICE = (
    "Voice and format (always):\n"
    "- Sound like a knowledgeable neighbor in Dorchester — warm, clear, human\n"
    "- If canonical_facts mark data as unavailable, say so plainly — never report 0 as a real count\n"
    "- Use natural paragraphs; avoid report headings and database jargon\n"
    "- NEVER paste a long bulleted or line-by-line list of records unless the user "
    "explicitly asked for a list\n"
    "- Put key totals in **bold**; do not bold every number\n"
    "- Explain what facts mean for residents (what was reported, what's still open, "
    "why it might matter)\n"
    "- Weave at most 2–3 specific examples into sentences — not a dump of rows\n"
    "- Do not mention SQL, queries, RAG, templates, Boston Open Data sync, or "
    "internal tools\n"
    "- Dorchester only; ignore data from other neighborhoods\n"
)


def classify_from_intent(intent: Optional[str]) -> AnswerKind:
    if not intent:
        return "civic_count"
    if intent == "911_unavailable":
        return "guardrail"
    if intent in EVENT_INTENTS:
        return "events"
    if intent in DETAIL_CIVIC_INTENTS:
        if intent.startswith("crime"):
            return "crime"
        return "civic_detail"
    if intent in COUNT_CIVIC_INTENTS:
        if intent.startswith("crime"):
            return "crime"
        if intent in ("311_top_types", "311_count_with_types"):
            return "civic_breakdown"
        return "civic_count"
    if intent.startswith("crime"):
        return "crime"
    return "civic_count"


def classify_from_bundle_parts(
    parts: Sequence[Dict[str, Any]],
    *,
    has_rag: bool = False,
) -> AnswerKind:
    if len(parts) > 1:
        return "multi_part"
    if has_rag:
        intents = {p.get("intent") for p in parts}
        if any(p.get("path") in ("sql", "guardrail") for p in parts):
            return "hybrid"
        if any(i == "transcript" for i in intents):
            return "rag_transcript"
        return "rag_general"
    if parts:
        return classify_from_intent(parts[0].get("intent"))
    return "civic_count"


def _kind_instructions(kind: AnswerKind) -> str:
    instructions: Dict[AnswerKind, str] = {
        "chitchat": (
            "Reply briefly and warmly to a greeting or pleasantry.\n"
            "- One or two short sentences\n"
            "- If conversation history exists, acknowledge it naturally (e.g. after "
            "thanks, reference what you just helped with)\n"
            "- Gently invite a civic question if appropriate — don't be pushy\n"
        ),
        "civic_count": (
            "Answer with the count and brief context (what was counted, time period).\n"
            "- 2–4 sentences\n"
            "- If issue_type_breakdown is present, summarize top categories in prose\n"
            "- Jump straight into the answer — no greeting line unless this is the first message\n"
        ),
        "civic_breakdown": (
            "The user wants more detail on categories or types — go deeper than a "
            "prior summary.\n"
            "- Cover the main categories from the data in grouped prose (e.g. parking, "
            "trash, property issues)\n"
            "- Give counts for the leading categories; explain what they mean in plain "
            "language\n"
            "- Do not repeat verbatim what you already said in conversation history\n"
            "- 1–3 short paragraphs\n"
        ),
        "civic_detail": (
            "The user asked about specific reports or a concern (e.g. potholes, recent "
            "complaints).\n"
            "- Summarize patterns: how many, open vs closed, common streets or areas\n"
            "- Mention 1–2 concrete examples inline (date/street) — not a roster of "
            "every row\n"
            "- If zero results, say so clearly and suggest a wider time window\n"
        ),
        "events": (
            "Describe what's happening in a friendly events-calendar tone.\n"
            "- Highlight a few interesting events with dates; group by day if helpful\n"
            "- Not a raw table dump — pick what matters for a resident planning their week\n"
            "- Jump straight into the answer — no greeting line unless this is the first message\n"
        ),
        "crime": (
            "Be factual and calm about safety statistics.\n"
            "- State counts clearly; note the time period and what is being counted\n"
            "- Avoid sensational language\n"
        ),
        "guardrail": (
            "Explain clearly what data is not available and what the user can ask instead.\n"
            "- Empathetic, not bureaucratic\n"
        ),
        "rag_general": (
            "Answer from community sources (news, policy, newsletters).\n"
            "- Cite source names naturally in sentences\n"
            "- Synthesize across sources; don't quote huge blocks\n"
            "- Only say 'I did not find exact information' when sources truly lack the topic\n"
        ),
        "rag_transcript": (
            "Summarize what residents said in meeting transcripts.\n"
            "- Cite the meeting/source naturally\n"
            "- Capture community voice and concerns in prose\n"
        ),
        "hybrid": (
            "Blend numeric facts with community context into one cohesive story.\n"
            "- Lead with verified numbers, then add resident voice or policy context\n"
            "- Explain why both matter for Dorchester\n"
        ),
        "multi_part": (
            "Answer EVERY part of the question — do not skip a topic.\n"
            "- One flowing answer with clear transitions between topics\n"
            "- If canonical_facts or data_unavailable say data is not in this app, explain once in prose — never treat placeholder zeros as real counts\n"
            "- Do not repeat the same unavailable-data disclaimer or redirect advice twice\n"
            "- Jump straight into the answer — no greeting line unless this is the first message\n"
        ),
        "history_followup": (
            "Continue the conversation using prior messages and cached data.\n"
            "- Expand or clarify — do not copy-paste the previous answer\n"
            "- If the new question is a different topic, do not reuse unrelated stats\n"
            "- Do NOT greet again — continue naturally from context\n"
        ),
    }
    return instructions.get(kind, instructions["civic_count"])


def is_follow_up_turn(
    conversation_history: Optional[List[Dict[str, str]]],
) -> bool:
    if not conversation_history:
        return False
    return any(msg.get("role") == "assistant" for msg in conversation_history)


def conversation_turn_instructions(
    conversation_history: Optional[List[Dict[str, str]]],
) -> str:
    if is_follow_up_turn(conversation_history):
        return (
            "Conversation turn:\n"
            "- This is a follow-up — do NOT open with greetings or pleasantries\n"
            "- Skip phrases like 'Hey there!', 'Glad you stopped by', 'Good to see you', etc.\n"
            "- Start directly with the answer or a natural transition (e.g. 'This week...', 'For June...')\n\n"
        )
    return (
        "Conversation turn:\n"
        "- First message in the thread — you may use one brief warm opener, then get to the point\n\n"
    )


_REPETITIVE_OPENERS = (
    re.compile(
        r"^hey there!\s*(?:glad you stopped by[.!]?\s*)?",
        re.IGNORECASE,
    ),
    re.compile(r"^glad you stopped by[.!]?\s*", re.IGNORECASE),
    re.compile(r"^good to see you[.!]?\s*", re.IGNORECASE),
    re.compile(r"^hi there[.!]?\s*", re.IGNORECASE),
)

_FALSE_ZERO_PATTERNS = (
    re.compile(
        r"(?i)\n?on the 911 side[^.!?]*[.!?]\s*",
    ),
    re.compile(
        r"(?i)[^.!?]*\b0\b[^.!?]*(?:911|emergency call)[^.!?]*[.!?]\s*",
    ),
    re.compile(
        r"(?i)[^.!?]*(?:fortunately|thankfully)[^.!?]*\b0\b[^.!?]*emergency[^.!?]*[.!?]\s*",
    ),
)


def trim_repetitive_opener(
    answer: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Strip canned greeting openers on follow-up turns."""
    if not is_follow_up_turn(conversation_history):
        return (answer or "").strip()
    out = (answer or "").strip()
    for pattern in _REPETITIVE_OPENERS:
        updated = pattern.sub("", out, count=1).strip()
        if updated != out:
            out = updated
            break
    return out


def _already_disclosed_unavailable(
    answer: str,
    *,
    intent: str,
    topic: str,
) -> bool:
    """True when the answer already explains that this data isn't available."""
    low = (answer or "").lower()
    if intent == "911_unavailable" or topic == "911":
        mentions_limit = any(
            phrase in low
            for phrase in (
                "don't have",
                "does not have",
                "doesn't have",
                "do not have",
                "not available",
                "no access",
                "isn't available",
                "is not available",
                "not in this app",
                "not in the app",
                "don't track",
                "do not track",
            )
        )
        return mentions_limit and "911" in low
    if intent == "unavailable" or topic:
        return any(
            phrase in low
            for phrase in ("not available", "don't have", "couldn't run", "can't answer")
        )
    return False


def fix_unavailable_data_claims(
    answer: str,
    blocks: Optional[Sequence[Dict[str, Any]]] = None,
) -> str:
    """Remove misleading zero-count claims when data is marked unavailable."""
    out = (answer or "").strip()
    if not out or not blocks:
        return out

    disclosures: List[str] = []
    for block in blocks:
        intent = str(block.get("intent") or "")
        path = str(block.get("path") or "")
        unavailable = (
            block.get("data_unavailable")
            or intent in UNAVAILABLE_DATA_INTENTS
            or path == "guardrail"
        )
        if not unavailable:
            continue
        canonical = (block.get("canonical_facts") or "").strip()
        topic = str(block.get("topic") or intent or "that topic")
        if intent == "911_unavailable" or topic == "911":
            for pattern in _FALSE_ZERO_PATTERNS:
                out = pattern.sub("", out).strip()
        if not canonical:
            continue
        if _already_disclosed_unavailable(out, intent=intent, topic=topic):
            continue
        if canonical.lower() not in out.lower():
            if "don't have" in canonical.lower() or "not available" in canonical.lower():
                disclosures.append(canonical)

    if disclosures:
        missing = [d for d in disclosures if d.lower() not in out.lower()]
        if missing:
            out = f"{out}\n\n{missing[0]}" if out else missing[0]
    return out.strip()


def polish_conversational_answer(
    answer: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    *,
    synthesis_blocks: Optional[Sequence[Dict[str, Any]]] = None,
    prepared_intent: Optional[str] = None,
) -> str:
    blocks: List[Dict[str, Any]] = list(synthesis_blocks or [])
    if prepared_intent and "911" in str(prepared_intent):
        if not any(b.get("intent") == "911_unavailable" for b in blocks):
            blocks.append({
                "intent": "911_unavailable",
                "topic": "911",
                "data_unavailable": True,
                "canonical_facts": (
                    "We don't have Boston **911 emergency dispatch call** data in this app. "
                    "911 call logs are separate from BPD crime incident reports and "
                    "311 service requests."
                ),
            })
    out = trim_repetitive_opener(answer, conversation_history)
    out = fix_unavailable_data_claims(out, blocks)
    return out.strip()


def system_prompt(
    kind: AnswerKind,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    return (
        "You are the Dorchester community assistant (On The Porch).\n\n"
        f"{CORE_VOICE}\n"
        f"{_kind_instructions(kind)}\n"
        f"{conversation_turn_instructions(conversation_history)}"
    )


def format_conversation_prefix(
    conversation_history: Optional[List[Dict[str, str]]],
    *,
    limit: int = 6,
) -> str:
    if not conversation_history:
        return ""
    lines: List[str] = []
    for msg in conversation_history[-limit:]:
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if role and content:
            lines.append(f"{role.upper()}: {content}")
    if not lines:
        return ""
    return "Recent conversation:\n" + "\n".join(lines) + "\n\n"


def resident_question_block(
    display_question: str,
    resolved_question: Optional[str] = None,
) -> str:
    display = (display_question or "").strip()
    resolved = (resolved_question or "").strip()
    if resolved and resolved.lower() != display.lower():
        return (
            f"What the resident asked:\n{display}\n\n"
            f"Data query used:\n{resolved}\n"
        )
    return f"Question:\n{display}\n"


def infer_last_topic_snippet(
    conversation_history: Optional[List[Dict[str, str]]],
) -> Optional[str]:
    if not conversation_history:
        return None
    for msg in reversed(conversation_history):
        if msg.get("role") != "assistant":
            continue
        text = (msg.get("content") or "").lower()
        if "311" in text or "service request" in text:
            return "311"
        if "event" in text:
            return "events"
        if any(h in text for h in ("shooting", "crime", "homicide", "safety")):
            return "safety"
        if "911" in text:
            return "911"
        break
    return None


def contextual_chitchat_reply(
    message: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Template chitchat with light conversation awareness (no extra LLM call)."""
    n = _normalize_small_talk(message)
    topic = infer_last_topic_snippet(conversation_history)

    if topic and (n.startswith(("thanks", "thank you", "thx", "ty")) or n in ("thanks", "thx", "ty")):
        topic_phrase = {
            "311": "the 311 info",
            "events": "the events",
            "safety": "the safety data",
            "911": "that",
        }.get(topic, "that")
        return (
            f"You're welcome — glad {topic_phrase} helped! "
            "Feel free to ask a follow-up or something new about Dorchester."
        )

    if topic and n in ("ok", "okay", "cool", "great", "got it", "nice"):
        return "Great! Let me know if you want to dig into anything else about Dorchester."

    return base_chitchat_reply(message)


def _normalize_small_talk(message: str) -> str:
    import re

    text = (message or "").strip().lower()
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def base_chitchat_reply(message: str) -> str:
    n = _normalize_small_talk(message)
    if any(x in n for x in ("how are you", "how r u", "how is it going", "how's it going")):
        return (
            "I'm doing well — thanks for asking! I'm here to help with Dorchester community "
            "info: events, 311 activity, safety trends, and neighborhood news. "
            "What would you like to know?"
        )
    if n.startswith(("thanks", "thank you", "thx", "ty")) or n in ("thanks", "thank you", "thx", "ty"):
        return (
            "You're welcome! Ask anytime about events, city services, safety, or what's "
            "happening in the neighborhood."
        )
    if n.startswith(("bye", "goodbye", "cya", "see ya", "see you")):
        return "Goodbye! Come back anytime you have questions about Dorchester."
    if "who are you" in n or "what are you" in n or "what can you" in n or "what do you do" in n:
        return (
            "I'm your Dorchester community assistant. I can help with local events, 311 requests, "
            "safety data, and neighborhood news. Try asking what's happening this week, or about "
            "recent 311 activity."
        )
    if n.startswith("good "):
        return (
            "Good to see you! Ask me about Dorchester events, services, safety, or neighborhood "
            "trends — what's on your mind?"
        )
    if n in ("help",) or "what can you help" in n or "how does this work" in n or "how do i use this" in n or n == "what is this":
        return (
            "I can answer questions about Dorchester events, 311 service requests, safety trends, "
            "and community news. Ask in your own words — I'll pull from local data and sources."
        )
    return (
        "Hi! I'm here to help with Dorchester community questions — events, services, safety, "
        "and local news. What would you like to know?"
    )
