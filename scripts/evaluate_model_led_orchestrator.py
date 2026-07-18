#!/usr/bin/env python3
"""Run the isolated orchestrator benchmark against one configured Gemini model."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PORCH = ROOT / "on_the_porch"
for candidate in (str(ROOT), str(PORCH)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import config  # noqa: E402
from orchestrator_evaluation import (  # noqa: E402
    ProviderResponse,
    evaluate_orchestrator,
    load_evaluation_dataset,
)


DATASETS = {
    "development": ROOT / "evaluation" / "model_led_orchestrator" / "development.json",
    "holdout": ROOT / "evaluation" / "model_led_orchestrator" / "sealed_holdout.json",
}


class GeminiEvaluationProvider:
    """One-call, no-retry adapter over the repository's installed Gemini SDK."""

    def __init__(self, model_name: str) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        try:
            import google.generativeai as genai  # type: ignore
        except ImportError as exc:
            raise RuntimeError("google-generativeai is not installed") from exc
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        response = self.model.generate_content(
            prompt,
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
            request_options={"timeout": max(1.0, float(timeout_seconds))},
        )
        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            text=_response_text(response),
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            provider_model=self.model_name,
        )


def _response_text(response: Any) -> str:
    try:
        text = response.text
        if text:
            return str(text).strip()
    except Exception:
        pass
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text:
                parts.append(str(text))
    return "".join(parts).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--allow-sealed-holdout", action="store_true")
    parser.add_argument("--deadline-seconds", type=float, default=8.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-decisions", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dataset == "holdout" and not args.allow_sealed_holdout:
        raise SystemExit("holdout evaluation requires --allow-sealed-holdout")
    dataset_name, sealed, cases = load_evaluation_dataset(
        DATASETS[args.dataset],
        allow_sealed_holdout=args.allow_sealed_holdout,
    )
    if args.limit:
        cases = cases[: max(0, args.limit)]
    provider = GeminiEvaluationProvider(args.model)
    report = evaluate_orchestrator(
        provider,
        cases,
        dataset_name=dataset_name,
        sealed=sealed,
        model_name=args.model,
        per_case_deadline_seconds=max(2.0, min(15.0, args.deadline_seconds)),
        include_decisions=args.include_decisions,
    )
    payload = report.to_safe_dict(include_decisions=args.include_decisions)
    output = args.output or Path(
        f"/private/tmp/{args.dataset}-{args.model.replace('/', '_')}-orchestrator.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps({
        "dataset": dataset_name,
        "model": args.model,
        "output": str(output),
        "metrics": payload["metrics"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
