"""Deterministic hash recipe for the authoritative runtime freeze record.

Recipe (fixed separators, sorted keys where JSON is used):

1. prompt_hash
   Build a function-mode semantic provider payload with empty trusted context and
   probe message ``__freeze_probe__``. Take the prompt text before
   ``RESIDENT_MESSAGE=``, then remove the trailing ``TRUSTED_TOKENS=...`` and
   ``TRUSTED_STATE=...`` lines. SHA-256 the remaining UTF-8 policy bytes.

2. planner_contract_hash
   ``SHA256(json.dumps(planner_function_declarations(), sort_keys=True,
   separators=(",", ":")))``.

3. compiler_hash / civic_311_module_hash
   SHA-256 of the respective module file bytes.

4. normalization_hash
   ``SHA256(b"resident-input-nfkc-v1|NFKC")``.

5. generation_config_sha256
   ``SHA256(json.dumps({"max_output_tokens": 900, "provider_attempts": 1,
   "temperature": 0}, sort_keys=True, separators=(",", ":")))``.

This module does not invent a second freeze format; it verifies
``evaluation/model_led_turn_service/authoritative_runtime_freeze.json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from model_led_orchestrator import normalize_resident_input
from semantic_planner import (
    SEMANTIC_PROMPT_VERSION,
    build_semantic_provider_payload,
    planner_function_declarations,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE_PATH = (
    ROOT / "evaluation" / "model_led_turn_service" / "authoritative_runtime_freeze.json"
)
COMPILER_PATH = ROOT / "on_the_porch" / "semantic_plan_compiler.py"
CIVIC_311_PATH = ROOT / "on_the_porch" / "civic_311.py"
NORMALIZATION_VERSION = "resident-input-nfkc-v1"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(payload: str) -> str:
    return _sha256_bytes(payload.encode("utf-8"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def semantic_prompt_policy_text(*, output_mode: str = "function") -> str:
    """Return the frozen planner policy text (instructions only)."""
    payload = build_semantic_provider_payload(
        normalize_resident_input("__freeze_probe__"),
        trusted_context={},
        available_trusted_reference_tokens=(),
        output_mode=output_mode,
    )
    policy_with_state = payload["prompt"].split("RESIDENT_MESSAGE=", 1)[0]
    lines = policy_with_state.splitlines()
    trimmed = [
        line
        for line in lines
        if not line.startswith("TRUSTED_TOKENS=")
        and not line.startswith("TRUSTED_STATE=")
    ]
    return "\n".join(trimmed)


def compute_freeze_hashes(*, root: Optional[Path] = None) -> Dict[str, Any]:
    """Compute the reproducible freeze hashes from current source files."""
    base = Path(root) if root is not None else ROOT
    policy = semantic_prompt_policy_text(output_mode="function")
    policy_bytes = policy.encode("utf-8")
    generation = {
        "max_output_tokens": 900,
        "provider_attempts": 1,
        "temperature": 0,
    }
    return {
        "planner_prompt_version": SEMANTIC_PROMPT_VERSION,
        "prompt_hash": _sha256_bytes(policy_bytes),
        "prompt_policy_bytes": len(policy_bytes),
        "planner_contract_hash": _sha256_text(
            _canonical_json(planner_function_declarations())
        ),
        "compiler_hash": _sha256_bytes(
            (base / "on_the_porch" / "semantic_plan_compiler.py").read_bytes()
        ),
        "civic_311_module_hash": _sha256_bytes(
            (base / "on_the_porch" / "civic_311.py").read_bytes()
        ),
        "normalization_version": NORMALIZATION_VERSION,
        "normalization_hash": _sha256_bytes(
            f"{NORMALIZATION_VERSION}|NFKC".encode("utf-8")
        ),
        "generation_config_sha256": _sha256_text(_canonical_json(generation)),
    }


def verify_authoritative_freeze(
    freeze_path: Optional[Path] = None,
    *,
    root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Compare committed freeze hashes against the current deterministic recipe."""
    path = Path(freeze_path) if freeze_path is not None else DEFAULT_FREEZE_PATH
    recorded = json.loads(path.read_text(encoding="utf-8"))
    computed = compute_freeze_hashes(root=root)
    keys = (
        "prompt_hash",
        "prompt_policy_bytes",
        "planner_contract_hash",
        "compiler_hash",
        "civic_311_module_hash",
        "normalization_hash",
        "generation_config_sha256",
        "planner_prompt_version",
    )
    mismatches = {
        key: {
            "recorded": recorded.get(key),
            "computed": computed.get(key),
        }
        for key in keys
        if recorded.get(key) != computed.get(key)
    }
    return (not mismatches), {
        "freeze_path": str(path),
        "computed": computed,
        "mismatches": mismatches,
    }


def load_authoritative_freeze(
    freeze_path: Optional[Path] = None,
) -> Mapping[str, Any]:
    path = Path(freeze_path) if freeze_path is not None else DEFAULT_FREEZE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "DEFAULT_FREEZE_PATH",
    "NORMALIZATION_VERSION",
    "compute_freeze_hashes",
    "load_authoritative_freeze",
    "semantic_prompt_policy_text",
    "verify_authoritative_freeze",
]
