"""LLM provider abstraction.

`complete_json` is a template method on the base class, and that is the whole
design. Caching, the call budget, JSON validation and the one validation retry
all live here, once. A provider subclass implements a single thing: turn a
prompt into raw text over the network.

Consequences worth stating, because they are the guarantees the rest of the
pipeline relies on:

* A cache hit returns before any subclass code runs, so it cannot touch the
  network and cannot spend budget.
* Every provider gets identical caching and validation behaviour for free, so
  swapping Groq for Gemini cannot quietly change pipeline semantics.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app import config
from backend.app.llm import cache

TModel = TypeVar("TModel", bound=BaseModel)

# Hard ceiling for one process. This is a prototype against a free API tier and
# a runaway loop is the expensive failure mode, so it fails loudly rather than
# degrading.
MAX_LLM_CALLS = 300

# Module-level, deliberately. There is one budget per process and threading it
# through every call site would add plumbing without adding control.
_calls_made = 0
_cache_hits = 0
_tokens_in = 0
_tokens_out = 0
_seconds = 0.0


class LLMError(RuntimeError):
    """Raised when the provider cannot produce a valid structured response."""


class LLMBudgetExceeded(RuntimeError):
    """Raised when a run tries to exceed MAX_LLM_CALLS."""


class LLMOfflineError(RuntimeError):
    """Raised on a cache miss while LLM_OFFLINE is set."""


def calls_made() -> int:
    return _calls_made


def cache_hits() -> int:
    return _cache_hits


def usage() -> dict[str, int | float]:
    total = _calls_made + _cache_hits
    return {
        "llm_calls": _calls_made,
        "cache_hits": _cache_hits,
        "requests": total,
        "cache_hit_rate": round(_cache_hits / total, 4) if total else 0.0,
        "prompt_tokens": _tokens_in,
        "completion_tokens": _tokens_out,
        "total_tokens": _tokens_in + _tokens_out,
        "api_seconds": round(_seconds, 3),
    }


def record_usage(prompt_tokens: int, completion_tokens: int, seconds: float) -> None:
    """Called by a provider after a completed network call."""
    global _tokens_in, _tokens_out, _seconds
    _tokens_in += prompt_tokens or 0
    _tokens_out += completion_tokens or 0
    _seconds += seconds


def reset_usage() -> None:
    global _calls_made, _cache_hits, _tokens_in, _tokens_out, _seconds
    _calls_made = 0
    _cache_hits = 0
    _tokens_in = 0
    _tokens_out = 0
    _seconds = 0.0


def charge() -> None:
    """Count one outbound request. Called by providers, per network attempt."""
    global _calls_made
    if _calls_made >= MAX_LLM_CALLS:
        raise LLMBudgetExceeded(
            f"refusing to exceed MAX_LLM_CALLS={MAX_LLM_CALLS}; "
            f"{_calls_made} calls already made this process"
        )
    _calls_made += 1


def _record_cache_hit() -> None:
    global _cache_hits
    _cache_hits += 1


class LLMProvider(ABC):
    """Base class. Subclasses implement `_generate` and nothing else."""

    name: str = "abstract"
    model: str = "unset"

    def complete_json(self, task_name: str, prompt: str, schema: Type[TModel]) -> dict:
        """Return a dict validated against `schema`.

        The cache key covers the prompt *and* the schema, so tightening a model
        invalidates its cached answers rather than silently reusing responses
        that no longer fit.
        """
        payload = {"prompt": prompt, "schema": schema.model_json_schema()}
        key = cache.cache_key(self.name, self.model, task_name, payload)

        cached = cache.load(task_name, key)
        if cached is not None:
            _record_cache_hit()
            return schema.model_validate(cached).model_dump()

        if config.LLM_OFFLINE:
            built_by = sorted(cache.providers_present(task_name))
            mine = f"{self.name}/{self.model}"
            held = ", ".join(built_by) if built_by else "nothing"

            if built_by and mine not in built_by:
                why = (f"That cache was built by {held}, and this process is "
                       f"configured as {mine}. Cache keys include the provider, "
                       f"so one provider cannot read another's answers — set "
                       f"LLM_PROVIDER to match.")
            else:
                why = (f"This process is configured as {mine}; the cache for this "
                       f"task holds answers from {held}. The input itself has no "
                       f"cached answer, so it has genuinely never been computed.")

            raise LLMOfflineError(
                f"LLM_OFFLINE is set and task {task_name!r} missed the cache "
                f"(key {key[:12]}...). {why} Re-run with a key to compute it, or "
                f"fix the configuration — but do not treat this as an empty result."
            )

        raw = self._generate(task_name, prompt, schema)
        try:
            parsed = schema.model_validate_json(raw)
        except ValidationError as first_error:
            # One retry, with the validator's own complaint fed back. Models
            # correct structural mistakes reliably when shown them; a second
            # retry mostly buys latency.
            repair_prompt = (
                f"{prompt}\n\n"
                f"Your previous response was rejected by the schema validator.\n"
                f"Previous response:\n{raw}\n\n"
                f"Validation errors:\n{first_error}\n\n"
                f"Return corrected JSON that satisfies the schema exactly."
            )
            raw = self._generate(task_name, repair_prompt, schema)
            try:
                parsed = schema.model_validate_json(raw)
            except ValidationError as second_error:
                raise LLMError(
                    f"{self.name}/{self.model} returned invalid JSON for task "
                    f"'{task_name}' twice. Second error: {second_error}"
                ) from second_error

        response = parsed.model_dump()
        cache.store(task_name, key, provider=self.name, model=self.model,
                    request=payload, response=response)
        return response

    @abstractmethod
    def _generate(self, task_name: str, prompt: str, schema: Type[TModel]) -> str:
        """Send `prompt`, return the raw response text.

        The schema is passed through so a provider that supports constrained
        decoding can use it. That is not a nicety: asked for eight objects under
        plain JSON mode, the model silently returned five, and a batch stage that
        drops records without erroring is worse than one that fails. Providers
        without constrained decoding may ignore it -- the base class validates
        the result either way.

        Must call `charge()` once per network attempt.
        """


def get_provider(name: str | None = None) -> LLMProvider:
    """Resolve the configured provider. The only place a provider is chosen."""
    name = (name or config.LLM_PROVIDER).strip().lower()
    if name == "groq":
        from backend.app.llm.groq_provider import GroqProvider

        return GroqProvider()
    if name == "gemini":
        from backend.app.llm.gemini_provider import GeminiProvider

        return GeminiProvider()
    raise ValueError(f"unknown LLM_PROVIDER '{name}' (expected 'groq' or 'gemini')")


def schema_hint(schema: Type[BaseModel]) -> str:
    """Render a schema for inclusion in a prompt. Groq's JSON mode needs the
    word 'JSON' and the shape in the prompt itself, not only in a parameter."""
    return json.dumps(schema.model_json_schema(), indent=2)
