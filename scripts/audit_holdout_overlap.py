#!/usr/bin/env python3
"""Audit a candidate holdout for overlap with development, prior holdouts, and prompts.

Exit codes:
  0 — no suspicious overlap
  1 — suspicious overlap found
  2 — usage / load error
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evaluation" / "model_led_turn_service"
DEFAULT_CANDIDATE = EVAL_DIR / "phase2_cleanup_sealed_holdout_v20.json"

REFERENCE_DATASETS = (
    EVAL_DIR / "phase2a_generalization.json",
    EVAL_DIR / "phase2b_integration_holdout.json",
    EVAL_DIR / "phase2b_integration_holdout_v2.json",
    EVAL_DIR / "phase2b_integration_holdout_v3.json",
    EVAL_DIR / "phase2b_integration_holdout_v4.json",
    EVAL_DIR / "phase2b_integration_holdout_v5.json",
    EVAL_DIR / "phase2b_integration_holdout_v6.json",
    EVAL_DIR / "phase2b_integration_holdout_v7.json",
    EVAL_DIR / "phase2b_integration_holdout_v8.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v9.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v10.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v11.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v12.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v13.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v14.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v15.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v16.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v17.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v18.json",
    EVAL_DIR / "phase2_cleanup_sealed_holdout_v19.json",
)

PROMPT_SOURCE = ROOT / "on_the_porch" / "semantic_planner.py"
ACCEPTANCE_SCRIPTS = (
    ROOT / "scripts" / "test_model_led_turn_service_live.py",
    ROOT / "scripts" / "test_hybrid_311_model_live.py",
    ROOT / "scripts" / "test_semantic_dialogue_controller_live.py",
    ROOT / "scripts" / "test_state_aware_event_retrieval_live.py",
)

NORMALIZE_RE = re.compile(r"[^a-z0-9\s]+", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
STRING_LITERAL_RE = re.compile(r"""(?P<q>['"])(?P<body>(?:\\.|(?!\1).)*)(?P=q)""")

# Civic boilerplate that should not alone trigger a phrase hit.
STOP_NGRAMS = {
    "dorchester",
    "boston 311",
    "last month",
    "prior month",
    "this week",
    "the rest of this week",
    "rest of this week through",
    "next week",
    "codman square",
    "fields corner",
    "service request",
    "service requests",
    "calendar week",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate",
        type=Path,
        default=DEFAULT_CANDIDATE,
        help="Path to the candidate holdout JSON",
    )
    parser.add_argument(
        "--ngram",
        type=int,
        default=5,
        help="N-gram size for phrase similarity (default: 5)",
    )
    parser.add_argument(
        "--jaccard-threshold",
        type=float,
        default=0.55,
        help="Token Jaccard threshold for near-paraphrase flagging",
    )
    return parser.parse_args()


def normalize(text: str) -> str:
    lowered = text.casefold()
    lowered = NORMALIZE_RE.sub(" ", lowered)
    return WHITESPACE_RE.sub(" ", lowered).strip()


def tokens(text: str) -> List[str]:
    return [tok for tok in normalize(text).split(" ") if tok]


def ngrams(words: Sequence[str], n: int) -> Set[str]:
    if len(words) < n:
        return set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def extract_messages(payload: Mapping[str, object], source: str) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for family in payload.get("families") or []:
        if not isinstance(family, Mapping):
            continue
        family_id = str(family.get("family_id") or "family")
        for index, message in enumerate(family.get("messages") or [], 1):
            rows.append((f"{source}:{family_id}:{index}", str(message)))
    for scenario in payload.get("scenarios") or []:
        if not isinstance(scenario, Mapping):
            continue
        scenario_id = str(scenario.get("scenario_id") or "scenario")
        for index, turn in enumerate(scenario.get("turns") or [], 1):
            if not isinstance(turn, Mapping):
                continue
            rows.append((f"{source}:{scenario_id}:{index}", str(turn.get("message") or "")))
    return rows


def load_json_messages(path: Path) -> List[Tuple[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    name = str(payload.get("dataset_name") or path.stem)
    return extract_messages(payload, name)


def extract_prompt_instruction_strings(path: Path) -> List[Tuple[str, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rows: List[Tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "instructions" not in targets:
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for index, elt in enumerate(node.value.elts, 1):
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                text = elt.value.strip()
                if len(text) >= 24:
                    rows.append((f"semantic_planner:instructions:{index}", text))
    # Also capture long string literals that look like planner policy lines.
    for index, match in enumerate(STRING_LITERAL_RE.finditer(source), 1):
        body = match.group("body")
        if len(body) >= 40 and any(
            key in body.casefold()
            for key in (
                "respond_directly",
                "ask_clarification",
                "gather_evidence",
                "trusted-summary",
                "prompt-injection",
            )
        ):
            rows.append((f"semantic_planner:literal:{index}", body))
    return rows


def extract_acceptance_strings(paths: Iterable[Path]) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # turn(client, thread, label, "message") or _turn(...)
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name not in {"turn", "_turn", "post_json", "post_sse"}:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    text = arg.value.strip()
                    if len(text) >= 12 and " " in text:
                        rows.append((f"acceptance:{path.name}", text))
            for keyword in node.keywords:
                if keyword.arg == "message" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        text = keyword.value.value.strip()
                        if len(text) >= 12 and " " in text:
                            rows.append((f"acceptance:{path.name}", text))
    # Deduplicate while preserving order.
    seen: Set[str] = set()
    unique: List[Tuple[str, str]] = []
    for label, text in rows:
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, text))
    return unique


def audit(
    candidate: Sequence[Tuple[str, str]],
    references: Sequence[Tuple[str, str]],
    *,
    n: int,
    jaccard_threshold: float,
) -> Dict[str, List[Mapping[str, object]]]:
    findings: Dict[str, List[Mapping[str, object]]] = {
        "exact": [],
        "normalized": [],
        "ngram": [],
        "jaccard": [],
    }
    ref_by_exact: Dict[str, List[str]] = defaultdict(list)
    ref_by_norm: Dict[str, List[str]] = defaultdict(list)
    ref_ngram_index: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    ref_token_rows: List[Tuple[str, str, List[str]]] = []

    for label, text in references:
        exact = text.strip()
        norm = normalize(text)
        ref_by_exact[exact].append(label)
        if norm:
            ref_by_norm[norm].append(label)
        words = tokens(text)
        ref_token_rows.append((label, text, words))
        for gram in ngrams(words, n):
            if gram in STOP_NGRAMS:
                continue
            ref_ngram_index[gram].append((label, text))

    for cand_label, cand_text in candidate:
        exact = cand_text.strip()
        norm = normalize(cand_text)
        words = tokens(cand_text)

        if exact in ref_by_exact:
            findings["exact"].append({
                "candidate": cand_label,
                "text": cand_text,
                "matches": sorted(set(ref_by_exact[exact])),
            })
        if norm and norm in ref_by_norm:
            # Skip if already reported as exact for the same references.
            matches = sorted(set(ref_by_norm[norm]))
            if exact not in ref_by_exact:
                findings["normalized"].append({
                    "candidate": cand_label,
                    "text": cand_text,
                    "matches": matches,
                })

        shared_grams: Dict[str, List[str]] = defaultdict(list)
        for gram in ngrams(words, n):
            if gram in STOP_NGRAMS:
                continue
            for ref_label, _ref_text in ref_ngram_index.get(gram, []):
                shared_grams[ref_label].append(gram)
        for ref_label, grams in shared_grams.items():
            unique_grams = sorted(set(grams))
            if len(unique_grams) >= 1 and max(len(g.split()) for g in unique_grams) >= n:
                # Flag when at least one full n-gram overlaps.
                findings["ngram"].append({
                    "candidate": cand_label,
                    "text": cand_text,
                    "match": ref_label,
                    "shared_ngrams": unique_grams[:8],
                })

        for ref_label, ref_text, ref_words in ref_token_rows:
            score = jaccard(words, ref_words)
            if score >= jaccard_threshold and abs(len(words) - len(ref_words)) <= 6:
                # Avoid double-counting exact/normalized hits.
                if normalize(cand_text) == normalize(ref_text):
                    continue
                findings["jaccard"].append({
                    "candidate": cand_label,
                    "text": cand_text,
                    "match": ref_label,
                    "reference_text": ref_text,
                    "score": round(score, 3),
                })

    # De-duplicate ngram/jaccard noise: keep strongest per candidate+match.
    deduped_ngram: Dict[Tuple[str, str], Mapping[str, object]] = {}
    for item in findings["ngram"]:
        key = (str(item["candidate"]), str(item["match"]))
        prev = deduped_ngram.get(key)
        if prev is None or len(item["shared_ngrams"]) > len(prev["shared_ngrams"]):
            deduped_ngram[key] = item
    findings["ngram"] = list(deduped_ngram.values())

    deduped_jaccard: Dict[Tuple[str, str], Mapping[str, object]] = {}
    for item in findings["jaccard"]:
        key = (str(item["candidate"]), str(item["match"]))
        prev = deduped_jaccard.get(key)
        if prev is None or float(item["score"]) > float(prev["score"]):
            deduped_jaccard[key] = item
    findings["jaccard"] = sorted(
        deduped_jaccard.values(),
        key=lambda row: float(row["score"]),
        reverse=True,
    )
    return findings


def print_report(
    candidate_path: Path,
    candidate_count: int,
    reference_count: int,
    findings: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    exact_n = len(findings["exact"])
    norm_n = len(findings["normalized"])
    ngram_n = len(findings["ngram"])
    jaccard_n = len(findings["jaccard"])
    suspicious = exact_n + norm_n + ngram_n + jaccard_n

    print(f"Candidate: {candidate_path}")
    print(f"Candidate messages: {candidate_count}")
    print(f"Reference corpus messages/snippets: {reference_count}")
    print("")
    print("Overlap summary")
    print(f"  exact matches:       {exact_n}")
    print(f"  normalized matches:  {norm_n}")
    print(f"  n-gram overlaps:     {ngram_n}")
    print(f"  high Jaccard pairs:  {jaccard_n}")
    print(f"  suspicious total:    {suspicious}")
    print("")

    def _preview(rows: Sequence[Mapping[str, object]], limit: int = 8) -> None:
        for row in rows[:limit]:
            print(f"  - {row.get('candidate')}: {row.get('text')!r}")
            if "matches" in row:
                print(f"    refs: {', '.join(row['matches'][:4])}")
            if "match" in row:
                extra = ""
                if "score" in row:
                    extra = f" score={row['score']}"
                if "shared_ngrams" in row:
                    extra = f" grams={row['shared_ngrams'][:3]}"
                print(f"    ref: {row['match']}{extra}")
                if "reference_text" in row:
                    print(f"    ref text: {row['reference_text']!r}")
        if len(rows) > limit:
            print(f"  ... {len(rows) - limit} more")

    if exact_n:
        print("Exact:")
        _preview(findings["exact"])
        print("")
    if norm_n:
        print("Normalized:")
        _preview(findings["normalized"])
        print("")
    if ngram_n:
        print("N-gram:")
        _preview(findings["ngram"])
        print("")
    if jaccard_n:
        print("Jaccard:")
        _preview(findings["jaccard"])
        print("")

    if suspicious:
        print("RESULT: FAIL — suspicious overlap detected")
    else:
        print("RESULT: PASS — no suspicious overlap detected")


def main() -> int:
    args = parse_args()
    candidate_path = args.candidate
    if not candidate_path.is_absolute():
        candidate_path = ROOT / candidate_path
    if not candidate_path.exists():
        print(f"candidate not found: {candidate_path}", file=sys.stderr)
        return 2

    try:
        candidate = load_json_messages(candidate_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"failed to load candidate: {exc}", file=sys.stderr)
        return 2

    references: List[Tuple[str, str]] = []
    for path in REFERENCE_DATASETS:
        if not path.exists():
            print(f"warning: missing reference dataset {path}", file=sys.stderr)
            continue
        references.extend(load_json_messages(path))

    if PROMPT_SOURCE.exists():
        references.extend(extract_prompt_instruction_strings(PROMPT_SOURCE))
    else:
        print(f"warning: missing prompt source {PROMPT_SOURCE}", file=sys.stderr)

    references.extend(extract_acceptance_strings(ACCEPTANCE_SCRIPTS))

    findings = audit(
        candidate,
        references,
        n=max(3, int(args.ngram)),
        jaccard_threshold=float(args.jaccard_threshold),
    )
    print_report(candidate_path, len(candidate), len(references), findings)

    suspicious = sum(len(findings[key]) for key in ("exact", "normalized", "ngram", "jaccard"))
    return 1 if suspicious else 0


if __name__ == "__main__":
    raise SystemExit(main())
