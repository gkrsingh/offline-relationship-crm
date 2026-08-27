"""Gemini provider.

The second provider is the test of whether the abstraction was real. It is:
this file implements `_generate` and nothing else. Caching, the call budget,
JSON validation, the one validation retry and the offline mode all still live in
`LLMProvider`, so switching `LLM_PROVIDER` cannot quietly change what the
pipeline means by a result.

Cache keys already include the provider name, so Groq's cached answers stay
valid and are simply never consulted while Gemini is active. Nothing is
invalidated by the switch and nothing is shared across it -- which is correct,
because two models are two different opinions.

Structured output uses `response_schema` + `response_mime_type`, which is
Gemini's constrained decoding. That is not a nicety: under plain JSON mode Groq
silently returned five people for a batch of eight, and a batch stage that drops
records without erroring is the worst failure mode available.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Type

from pydantic import BaseModel

from backend.app import config
from backend.app.llm.provider import LLMError, LLMProvider, charge, record_usage

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2.0
MAX_OUTPUT_TOKENS = 8192

SYSTEM_PREAMBLE = (
    "You are a careful data analyst. You return only valid JSON matching the "
    "requested schema. You never invent facts that are not in the input."
)

# Gemini reports quota exhaustion as 429 RESOURCE_EXHAUSTED, and transient
# capacity problems as 500/503. Both are worth waiting out; a 400 is a bug in
# our request and is not.
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s")


def _retry_after(exc: Exception, attempt: int) -> float:
    """Honour Google's own retryDelay hint when the error carries one."""
    match = _RETRY_DELAY.search(str(exc))
    if match:
        try:
            return min(90.0, float(match.group(1)) + 1.0)
        except ValueError:
            pass
    return BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)


def _is_retryable(exc: Exception) -> bool:
    text = str(exc)
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status in (429, 500, 502, 503, 504):
        return True
    return any(marker in text for marker in
               ("RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE", "INTERNAL"))


def _is_daily_quota(exc: Exception) -> bool:
    text = str(exc)
    return "PerDay" in text or "per day" in text.lower()


def _strip_titles(node: Any) -> Any:
    """Remove keys Gemini's schema validator rejects.

    Pydantic emits `title`, `default` and `$defs`/`$ref`; Gemini accepts a
    narrower OpenAPI subset. Rather than hand-maintain a second schema and let
    the two drift, the pydantic one is walked and trimmed -- so the schema the
    model is constrained to is always the schema the response is validated
    against.
    """
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items()
                if k not in ("title", "default", "additionalProperties")}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _inline_refs(schema: dict) -> dict:
    """Resolve $defs/$ref into a single inline schema."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(dict(defs.get(name, {})))
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve({k: v for k, v in schema.items() if k != "$defs"})


def gemini_schema(model: Type[BaseModel]) -> dict:
    """Pydantic JSON schema, reshaped into what Gemini will accept."""
    return _strip_titles(_inline_refs(model.model_json_schema()))


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        # No key required to construct. See GroqProvider for why: a cached run
        # must be able to start without credentials.
        self._key = api_key or config.GEMINI_API_KEY
        self.model = model or config.GEMINI_MODEL
        self._client_instance = None

    @property
    def _client(self):
        if self._client_instance is None:
            if not self._key:
                raise LLMError(
                    "No Gemini key found and this request missed the cache. Set "
                    "GEMINI_API_KEY (or GOOGLE_API_KEY, or either with an IA_ "
                    "prefix), or set LLM_OFFLINE=true to fail fast."
                )
            from google import genai

            self._client_instance = genai.Client(api_key=self._key)
        return self._client_instance

    def _generate(self, task_name: str, prompt: str,
                  schema: Type[BaseModel] | None = None) -> str:
        from google.genai import types

        settings: dict[str, Any] = {
            "system_instruction": SYSTEM_PREAMBLE,
            "temperature": 0,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "response_mime_type": "application/json",
        }
        if schema is not None:
            settings["response_schema"] = gemini_schema(schema)

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            charge()  # every attempt that leaves the process is counted
            started = time.monotonic()
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**settings),
                )
            except Exception as exc:  # noqa: BLE001 -- classified immediately below
                last_error = exc
                if _is_daily_quota(exc):
                    raise LLMError(
                        f"gemini daily quota is exhausted (task {task_name!r}). "
                        f"Everything already computed is in data/cache/llm and "
                        f"replays offline; re-run with --resume when it resets. "
                        f"Server said: {str(exc)[:240]}"
                    ) from exc
                if not _is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                    raise LLMError(
                        f"gemini failed task {task_name!r}: {str(exc)[:300]}") from exc
                time.sleep(_retry_after(exc, attempt))
                continue

            usage = getattr(response, "usage_metadata", None)
            record_usage(getattr(usage, "prompt_token_count", 0) or 0,
                         getattr(usage, "candidates_token_count", 0) or 0,
                         time.monotonic() - started)

            text = getattr(response, "text", None)
            if not text:
                # A blocked or empty candidate is not a schema problem, so the
                # base class's validation retry would not help. Fail loudly.
                reason = getattr(response, "prompt_feedback", None)
                raise LLMError(
                    f"gemini returned no text for task {task_name!r} "
                    f"(feedback: {reason})")
            return text

        raise LLMError(
            f"gemini exhausted {MAX_ATTEMPTS} attempts on task {task_name!r}: "
            f"{str(last_error)[:240]}") from last_error
