"""Post-answer validation for prepared SQL numeric responses (Phase 1)."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _first_count_from_result(result: Dict[str, Any]) -> Optional[int]:
    rows = result.get("rows") or []
    if not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    for key in ("cnt", "count", "labeled_cnt", "proxy_cnt"):
        if key in row and row[key] is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _bold_counts_in_answer(answer: str) -> list[int]:
    return [int(m.replace(",", "")) for m in re.findall(r"\*\*(\d[\d,]*)\*\*", answer or "")]


def validate_prepared_answer(
    answer: str,
    result: Dict[str, Any],
    *,
    prepared_intent: Optional[str] = None,
) -> str:
    """If the SQL result has a count, ensure the answer's lead number is consistent."""
    if not prepared_intent or not answer:
        return answer

    sql_count = _first_count_from_result(result)
    bold = _bold_counts_in_answer(answer)

    if prepared_intent == "crime_homicide_count":
        row = (result.get("rows") or [{}])[0] if result.get("rows") else {}
        labeled = int((row or {}).get("labeled_cnt") or 0)
        proxy = int((row or {}).get("proxy_cnt") or 0)
        if labeled > 0 and (not bold or bold[0] != labeled):
            return (
                f"There were **{labeled:,}** homicides in Dorchester "
                f"(offense labeled homicide or murder in Boston Open Data)."
            )
        if labeled == 0 and proxy > 0 and (not bold or proxy not in bold):
            return (
                f"I found **0** homicides explicitly labeled in Boston Open Data, "
                f"but **{proxy:,}** fatal-shooting death investigations in Dorchester."
            )
        return answer

    if sql_count is None:
        return answer

    if not bold:
        return answer

    if bold[0] != sql_count:
        period_match = re.search(r"for (the last \d+ days|this year[^.]*|this weekend)", answer, re.I)
        period = period_match.group(1) if period_match else "the selected period"
        label = prepared_intent.replace("_", " ").replace("crime ", "").replace("311 ", "311 ")
        return (
            f"There were **{sql_count:,}** {label} in Dorchester for {period} "
            "(based on the latest synced Boston Open Data)."
        )
    return answer
