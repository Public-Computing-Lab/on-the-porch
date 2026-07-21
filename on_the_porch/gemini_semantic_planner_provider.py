"""Maintained-SDK provider adapter for the shared semantic planner contract."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, Mapping, Optional

from orchestrator_evaluation import ProviderResponse
from sanitized_orchestrator_protocol import (
    planner_function_declarations,
    sanitized_generation_config,
    split_semantic_planner_prompt,
)


class GeminiSemanticPlannerProvider:
    """One-attempt Gemini adapter with bounded, function-call output."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        output_mode: str,
        capture_responses: bool = False,
    ) -> None:
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if output_mode not in {"plain", "function"}:
            raise ValueError("output mode must be function or plain")
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as exc:
            raise RuntimeError("the maintained google-genai SDK is not installed") from exc
        self._api_key = api_key
        self._genai = genai
        self._types = types
        self.model_name = model_name
        self.output_mode = output_mode
        self.capture_responses = bool(capture_responses)
        self.raw_responses: list[dict[str, Any]] = []
        self.provider_errors: list[dict[str, Any]] = []
        self.call_count = 0
        self._result_lock = threading.Lock()

    def generate(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        call_index = self._next_call_index()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="semantic-planner")
        future = executor.submit(
            self._generate_sync,
            prompt,
            call_index=call_index,
            transport_timeout_seconds=timeout_seconds,
        )
        try:
            return future.result(timeout=max(0.05, float(timeout_seconds)))
        except FutureTimeoutError as exc:
            future.cancel()
            timeout = TimeoutError("semantic planner provider deadline exhausted")
            self._record_error(call_index, timeout)
            raise timeout from exc
        except Exception as exc:
            self._record_error(call_index, exc)
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    async def generate_async(self, prompt: str, *, timeout_seconds: float) -> ProviderResponse:
        call_index = self._next_call_index()
        try:
            return await asyncio.wait_for(
                self._generate_async_request(
                    prompt,
                    call_index=call_index,
                    transport_timeout_seconds=timeout_seconds,
                ),
                timeout=max(0.05, float(timeout_seconds)),
            )
        except asyncio.TimeoutError as exc:
            timeout = TimeoutError("semantic planner provider deadline exhausted")
            self._record_error(call_index, timeout)
            raise timeout from exc
        except Exception as exc:
            self._record_error(call_index, exc)
            raise

    def _generate_sync(
        self,
        prompt: str,
        *,
        call_index: int,
        transport_timeout_seconds: float,
    ) -> ProviderResponse:
        client = self._client(transport_timeout_seconds)
        system_instruction, resident_content = split_semantic_planner_prompt(prompt)
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=resident_content,
                config=self._generation_config(system_instruction=system_instruction),
            )
            return self._normalize_response(response, call_index=call_index)
        finally:
            client.close()

    async def _generate_async_request(
        self,
        prompt: str,
        *,
        call_index: int,
        transport_timeout_seconds: float,
    ) -> ProviderResponse:
        client = self._client(transport_timeout_seconds)
        system_instruction, resident_content = split_semantic_planner_prompt(prompt)
        try:
            response = await client.aio.models.generate_content(
                model=self.model_name,
                contents=resident_content,
                config=self._generation_config(system_instruction=system_instruction),
            )
            return self._normalize_response(response, call_index=call_index)
        finally:
            await client.aio.aclose()

    def _client(self, timeout_seconds: float):
        # Gemini currently rejects manually configured deadlines below 10s.
        # The public generate methods enforce the smaller application deadline.
        timeout_ms = max(10_000, int(max(1.0, float(timeout_seconds)) * 1000))
        return self._genai.Client(
            api_key=self._api_key,
            http_options=self._types.HttpOptions(
                timeout=timeout_ms,
                retry_options=self._types.HttpRetryOptions(attempts=1),
            ),
        )

    def _generation_config(self, *, system_instruction: Optional[str] = None):
        generation = sanitized_generation_config(output_mode=self.output_mode)
        kwargs: Dict[str, Any] = {
            "temperature": generation["temperature"],
            "max_output_tokens": generation["max_output_tokens"],
        }
        if system_instruction is not None:
            kwargs["system_instruction"] = system_instruction
        if self.output_mode == "plain":
            kwargs["response_mime_type"] = generation["response_mime_type"]
        else:
            declarations = [
                self._types.FunctionDeclaration(
                    name=item["name"],
                    description=item["description"],
                    parameters_json_schema=item["parameters"],
                )
                for item in planner_function_declarations()
            ]
            kwargs.update({
                "tools": [self._types.Tool(function_declarations=declarations)],
                "tool_config": self._types.ToolConfig(
                    function_calling_config=self._types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=[item.name for item in declarations],
                    ),
                ),
                "automatic_function_calling": self._types.AutomaticFunctionCallingConfig(
                    disable=True,
                ),
            })
        return self._types.GenerateContentConfig(**kwargs)

    def _normalize_response(self, response: Any, *, call_index: int) -> ProviderResponse:
        if self.output_mode == "function":
            calls = []
            for candidate in getattr(response, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    call = getattr(part, "function_call", None)
                    if call is not None:
                        calls.append(call)
            if len(calls) != 1:
                raise ValueError("provider did not return exactly one planner function call")
            text = json.dumps(
                {
                    "action": str(calls[0].name or ""),
                    "arguments": _json_safe_mapping(calls[0].args),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
        else:
            text = response_text(response)
        if self.capture_responses:
            with self._result_lock:
                self.raw_responses.append({"call_index": call_index, "response": text})
        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            provider_model=self.model_name,
            metadata=_safe_response_metadata(response),
        )

    def _next_call_index(self) -> int:
        with self._result_lock:
            self.call_count += 1
            return self.call_count

    def _record_error(self, call_index: int, exc: Exception) -> None:
        if not self.capture_responses:
            return
        with self._result_lock:
            self.provider_errors.append({
                "call_index": call_index,
                "error_type": type(exc).__name__[:80],
                "message": str(exc)[:500],
            })


def response_text(response: Any) -> str:
    try:
        if response.text:
            return str(response.text).strip()
    except Exception:
        pass
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                parts.append(str(part.text))
    return "".join(parts).strip()


def _json_safe_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return json.loads(json.dumps(dict(raw), ensure_ascii=True, allow_nan=False))
    raise ValueError("planner function arguments are not an object")


def _safe_response_metadata(response: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    model_version = getattr(response, "model_version", None)
    response_id = getattr(response, "response_id", None)
    if isinstance(model_version, str):
        result["model_version"] = model_version[:100]
    if isinstance(response_id, str):
        result["response_id"] = response_id[:120]
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if finish_reason is not None:
            result["finish_reason"] = str(finish_reason)[:80]
    return result


__all__ = ["GeminiSemanticPlannerProvider", "response_text"]
