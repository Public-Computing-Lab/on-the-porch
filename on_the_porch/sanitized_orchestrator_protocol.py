"""Privacy-audited compact semantic protocol for offline provider evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping

from model_led_orchestrator import NormalizedResidentInput
from semantic_planner import (
    SEMANTIC_PROTOCOL_VERSION,
    SemanticPlan,
    audit_semantic_provider_payload,
    build_semantic_planner_prompt,
    build_semantic_provider_payload,
    semantic_plan_response_schema,
)


PUBLIC_PROTOCOL_VERSION = SEMANTIC_PROTOCOL_VERSION
PUBLIC_CAPABILITY_CATALOG_VERSION = "not-model-facing"
PUBLIC_SCHEMA_VERSION = 1
PUBLIC_CONFIDENCE_THRESHOLD = 0.70


@dataclass(frozen=True)
class OutboundPayloadAudit:
    protocol_version: str
    payload_bytes: int
    payload_sha256: str
    capability_count: int
    trusted_reference_count: int

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "capability_count": self.capability_count,
            "trusted_reference_count": self.trusted_reference_count,
        }


class SanitizedPublicEvaluationProtocol:
    """Provider-neutral protocol used by the isolated evaluation runner."""

    def build_prompt(
        self,
        resident_input: NormalizedResidentInput,
        *,
        trusted_context: Mapping[str, Any],
        available_trusted_reference_tokens: Iterable[str],
    ) -> str:
        return build_semantic_planner_prompt(
            resident_input,
            trusted_context=trusted_context,
            available_trusted_reference_tokens=available_trusted_reference_tokens,
        )

    def parse_decision(
        self,
        raw: str,
        *,
        available_trusted_reference_tokens: Iterable[str],
    ) -> SemanticPlan:
        return SemanticPlan.from_json(
            raw,
            available_trusted_reference_tokens=available_trusted_reference_tokens,
        )


def build_sanitized_provider_payload(
    resident_input: NormalizedResidentInput,
    *,
    trusted_context: Mapping[str, Any],
    available_trusted_reference_tokens: Iterable[str] = (),
) -> Dict[str, Any]:
    return build_semantic_provider_payload(
        resident_input,
        trusted_context=trusted_context,
        available_trusted_reference_tokens=available_trusted_reference_tokens,
    )


def build_sanitized_orchestrator_prompt(
    resident_input: NormalizedResidentInput,
    *,
    trusted_context: Mapping[str, Any],
    available_trusted_reference_tokens: Iterable[str] = (),
) -> str:
    return build_semantic_planner_prompt(
        resident_input,
        trusted_context=trusted_context,
        available_trusted_reference_tokens=available_trusted_reference_tokens,
    )


def audit_sanitized_provider_payload(payload: Mapping[str, Any]) -> OutboundPayloadAudit:
    # Generation-config-only audit calls remain supported for the CLI.
    if set(payload) == {"generation_config"}:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        import hashlib

        return OutboundPayloadAudit(
            PUBLIC_PROTOCOL_VERSION,
            len(encoded.encode("utf-8")),
            hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            0,
            0,
        )
    result = audit_semantic_provider_payload(payload)
    return OutboundPayloadAudit(
        protocol_version=result["protocol_version"],
        payload_bytes=result["payload_bytes"],
        payload_sha256=result["payload_sha256"],
        capability_count=0,
        trusted_reference_count=result["trusted_reference_count"],
    )


def parse_sanitized_orchestrator_decision(
    raw: str,
    *,
    available_trusted_reference_tokens: Iterable[str] = (),
) -> SemanticPlan:
    return SemanticPlan.from_json(
        raw,
        available_trusted_reference_tokens=available_trusted_reference_tokens,
    )


def sanitized_generation_config(*, output_mode: str = "native") -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "temperature": 0,
        "max_output_tokens": 1400,
        "response_mime_type": "application/json",
    }
    if output_mode == "native":
        config["response_schema"] = semantic_plan_response_schema()
    elif output_mode != "plain":
        raise ValueError("output mode must be native or plain")
    return config


__all__ = [
    "PUBLIC_CAPABILITY_CATALOG_VERSION",
    "PUBLIC_CONFIDENCE_THRESHOLD",
    "PUBLIC_PROTOCOL_VERSION",
    "PUBLIC_SCHEMA_VERSION",
    "OutboundPayloadAudit",
    "SanitizedPublicEvaluationProtocol",
    "audit_sanitized_provider_payload",
    "build_sanitized_orchestrator_prompt",
    "build_sanitized_provider_payload",
    "parse_sanitized_orchestrator_decision",
    "sanitized_generation_config",
    "semantic_plan_response_schema",
]
