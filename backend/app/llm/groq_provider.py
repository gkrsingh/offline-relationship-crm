"""Groq provider: JSON mode, temperature 0, exponential backoff on 429."""

from __future__ import annotations

import random
import re
import time
from typing import Type

from groq import Groq
from groq import APIStatusError, BadRequestError, RateLimitError
from pydantic import BaseModel

from backend.app import config
from backend.app.llm.provider import LLMError, LLMProvider, charge, record_usage

# Three attempts total, doubling. Groq's free tier rate-limits per minute, so a
# short backoff clears most of it; anything longer and the pipeline should just
# fail and be re-run against the cache it already built.
BACKOFF_BASE_SECONDS = 2.0
MAX_ATTEMPTS = 3

# The free tier meters prompt + reserved completion against a tokens-per-minute
# budget, so reserving generously gets the request rejected outright with a 413.
# Sized to leave headroom under an 8k TPM ceiling at the configured batch size.
MAX_COMPLETION_TOKENS = 3000

# Groq reports rate limiting as 429 and, when one request exceeds the whole
# per-minute budget, as 413 with code `rate_limit_exceeded`. Both are worth
# waiting out; a 413 that genuinely means 'payload too large' is not, so the
# code is checked rather than the status alone.
RATE_LIMIT_STATUSES = (413, 429)

# Groq meters tokens per MINUTE and counts the reserved completion budget
# against it, so a run that ignores the headers spends its whole quota in the
# first ten seconds and then 429s for the rest of the job. The provider reads
# what the server tells it and waits when the remaining budget is too small for
# the next request -- which turns a run that mostly fails into one that mostly
# succeeds, at the same total cost.
TOKEN_HEADROOM = 1.15


def _parse_seconds(raw: str | None) -> float:
    if not raw:
        return 0.0
    text = str(raw).strip()
    if text.endswith("ms"):
        try:
            return float(text[:-2]) / 1000.0
        except ValueError:
            return 0.0
    total, number = 0.0, ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
        elif char == "m":
            total += float(number or 0) * 60.0
            number = ""
        elif char == "s":
            total += float(number or 0)
            number = ""
    return total + (float(number) if number else 0.0)


def _retry_after(exc: Exception, attempt: int) -> float:
    """Honour the server's own wait hint when it gives one.

    Guessing a backoff against a per-minute budget either wastes time or trips
    the limit again. The header knows; jittered exponential is the fallback.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for key in ("retry-after", "x-ratelimit-reset-tokens",
                "x-ratelimit-reset-requests"):
        raw = headers.get(key)
        if not raw:
            continue
        try:
            return min(90.0, float(str(raw).rstrip("s")) + 1.0)
        except ValueError:
            continue
    return BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)


SYSTEM_PREAMBLE = (
    "You are a careful data analyst. You return only valid JSON matching the "
    "requested schema. You never invent facts that are not in the input."
)


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        # The key is NOT required here. A cache hit returns from the base class
        # before `_generate` runs, so a fully-cached run must not need one --
        # demanding it at construction made the deployed demo impossible to
        # start, which is the failure this whole caching layer exists to avoid.
        self._key = api_key or config.GROQ_API_KEY
        self.model = model or config.GROQ_MODEL
        self._client_instance = None
        self._supports_json_schema = True

    @property
    def _client(self):
        """Built on first use, so no key is needed to serve from cache."""
        if self._client_instance is None:
            if not self._key:
                raise LLMError(
                    "GROQ_API_KEY is not set and this request missed the cache. "
                    "Add a key to .env, or set LLM_OFFLINE=true to fail fast "
                    "instead of reaching for the network."
                )
            self._client_instance = Groq(api_key=self._key,
                                         timeout=config.LLM_TIMEOUT_SECONDS)
        return self._client_instance
        self._tokens_left: float | None = None
        self._tokens_reset: float = 0.0

    def _wait_for_budget(self, needed: int) -> None:
        """Sleep until the per-minute token budget can afford the next call."""
        if self._tokens_left is None or self._tokens_left >= needed * TOKEN_HEADROOM:
            return
        delay = min(70.0, self._tokens_reset + 1.0)
        if delay > 0:
            time.sleep(delay)
        self._tokens_left = None

    def _note_budget(self, response) -> None:
        headers = getattr(response, "headers", None) or {}
        remaining = headers.get("x-ratelimit-remaining-tokens")
        if remaining is None:
            return
        try:
            self._tokens_left = float(remaining)
        except ValueError:
            return
        self._tokens_reset = _parse_seconds(headers.get("x-ratelimit-reset-tokens"))

    def _response_format(self, task_name: str, schema: Type[BaseModel] | None) -> dict:
        """Constrained decoding when the model supports it, plain JSON otherwise.

        Plain `json_object` mode is only a promise that the output parses. With a
        batch of eight people it returned five and parsed cleanly, which is the
        worst kind of failure: silent and well-formed. `json_schema` constrains
        generation to the shape, so a short answer becomes impossible rather than
        merely unlikely.
        """
        if schema is None or not self._supports_json_schema:
            return {"type": "json_object"}
        name = re.sub(r"[^a-zA-Z0-9_]+", "_", task_name)[:60] or "response"
        return {"type": "json_schema",
                "json_schema": {"name": name, "schema": schema.model_json_schema()}}

    def _generate(self, task_name: str, prompt: str,
                  schema: Type[BaseModel] | None = None) -> str:
        last_error: Exception | None = None

        # Rough but sufficient: four characters to the token, plus whatever we
        # are reserving for the answer.
        needed = len(prompt) // 4 + MAX_COMPLETION_TOKENS

        for attempt in range(MAX_ATTEMPTS):
            self._wait_for_budget(needed)
            charge()  # every attempt that leaves the process is counted
            started = time.monotonic()
            try:
                raw_response = self._client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PREAMBLE},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=self._response_format(task_name, schema),
                    temperature=0,
                    seed=42,
                    max_completion_tokens=MAX_COMPLETION_TOKENS,
                )
                self._note_budget(raw_response)
                completion = raw_response.parse()
            except BadRequestError as exc:
                # Some models advertise json_schema and then reject a particular
                # shape. Drop to plain JSON mode once rather than failing the run.
                if self._supports_json_schema:
                    self._supports_json_schema = False
                    last_error = exc
                    continue
                raise LLMError(
                    f"groq rejected the request for task {task_name!r}: {exc}") from exc
            except RateLimitError as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS - 1:
                    break
                time.sleep(_retry_after(exc, attempt))
                continue
            except APIStatusError as exc:
                last_error = exc
                retryable = (exc.status_code >= 500
                             or (exc.status_code in RATE_LIMIT_STATUSES
                                 and "rate_limit" in str(exc)))
                if not retryable or attempt == MAX_ATTEMPTS - 1:
                    raise LLMError(
                        f"groq returned {exc.status_code} for task '{task_name}': {exc}"
                    ) from exc
                time.sleep(_retry_after(exc, attempt))
                continue

            elapsed = time.monotonic() - started
            usage = getattr(completion, "usage", None)
            record_usage(getattr(usage, "prompt_tokens", 0),
                         getattr(usage, "completion_tokens", 0), elapsed)

            content = completion.choices[0].message.content
            if not content:
                raise LLMError(f"groq returned an empty response for task '{task_name}'")
            return content

        detail = str(last_error)
        if "tokens per day" in detail or "(TPD)" in detail:
            # A per-minute limit is worth waiting out; a per-day one is not.
            # Say so plainly instead of letting the caller read a stack trace and
            # conclude the pipeline is broken.
            raise LLMError(
                f"groq daily token quota is exhausted (task {task_name!r}). "
                f"Everything already computed is in data/cache/llm and replays "
                f"offline; re-run with --resume when the quota resets. "
                f"Server said: {detail[:240]}"
            ) from last_error
        raise LLMError(
            f"groq rate-limited task '{task_name}' after {MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error
