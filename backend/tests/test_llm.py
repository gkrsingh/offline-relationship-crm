"""LLM layer: cache keying, cache-before-network, budget, validation retry.

No network. A fake provider stands in for Groq, which is enough to test
everything the base class owns -- and everything the base class owns is
exactly what would otherwise differ between providers.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from backend.app.llm import cache
from backend.app.llm import provider as llm


class Answer(BaseModel):
    verdict: str
    confidence: float


class FakeProvider(llm.LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, responses=None):
        self.prompts: list[str] = []
        self.responses = list(responses or ['{"verdict": "same_person", "confidence": 0.9}'])

    def _generate(self, task_name: str, prompt: str, schema=None) -> str:
        llm.charge()
        self.prompts.append(prompt)
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "llm")
    monkeypatch.setattr(llm.config, "LLM_OFFLINE", False)
    llm.reset_usage()
    yield


# ---------------------------------------------------------------------------
# Cache keying
# ---------------------------------------------------------------------------

def test_key_is_stable_across_dict_ordering():
    a = cache.cache_key("groq", "m", "task", {"x": 1, "y": 2})
    b = cache.cache_key("groq", "m", "task", {"y": 2, "x": 1})
    assert a == b


@pytest.mark.parametrize("changed", [
    {"provider": "gemini"}, {"model": "other"}, {"task_name": "other"},
    {"payload": {"x": 2}},
])
def test_key_changes_when_any_component_changes(changed):
    base = dict(provider="groq", model="m", task_name="task", payload={"x": 1})
    assert cache.cache_key(**base) != cache.cache_key(**{**base, **changed})


def test_key_is_a_sha256_hex_digest():
    key = cache.cache_key("groq", "m", "task", {"x": 1})
    assert len(key) == 64 and set(key) <= set("0123456789abcdef")


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

def test_second_identical_call_is_a_cache_hit_and_does_not_call_the_model():
    provider = FakeProvider()

    first = provider.complete_json("t", "prompt", Answer)
    assert llm.calls_made() == 1 and llm.cache_hits() == 0

    second = provider.complete_json("t", "prompt", Answer)
    assert second == first
    assert llm.calls_made() == 1, "a cache hit must not increment the call counter"
    assert llm.cache_hits() == 1
    assert len(provider.prompts) == 1, "a cache hit must not reach the provider"


def test_a_different_prompt_is_a_miss():
    provider = FakeProvider()
    provider.complete_json("t", "prompt one", Answer)
    provider.complete_json("t", "prompt two", Answer)
    assert llm.calls_made() == 2


def test_changing_the_schema_invalidates_the_cache():
    class Wider(BaseModel):
        verdict: str
        confidence: float
        reason: str = "unset"

    provider = FakeProvider()
    provider.complete_json("t", "prompt", Answer)
    provider.complete_json("t", "prompt", Wider)
    assert llm.calls_made() == 2


def test_cache_files_are_readable_json_on_disk():
    provider = FakeProvider()
    provider.complete_json("dedupe_adjudication", "prompt", Answer)
    files = list((cache.CACHE_DIR / "dedupe_adjudication").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["provider"] == "fake"
    assert payload["response"]["verdict"] == "same_person"
    assert payload["key"] == files[0].stem


def test_a_corrupt_cache_file_is_a_miss_not_a_crash():
    provider = FakeProvider()
    provider.complete_json("t", "prompt", Answer)
    corrupt = next((cache.CACHE_DIR / "t").glob("*.json"))
    corrupt.write_text("{not json", encoding="utf-8")
    provider.complete_json("t", "prompt", Answer)
    assert llm.calls_made() == 2


# ---------------------------------------------------------------------------
# Offline mode
# ---------------------------------------------------------------------------

def test_offline_mode_serves_cache_and_refuses_the_network(monkeypatch):
    provider = FakeProvider()
    provider.complete_json("t", "prompt", Answer)

    monkeypatch.setattr(llm.config, "LLM_OFFLINE", True)
    assert provider.complete_json("t", "prompt", Answer)["verdict"] == "same_person"

    with pytest.raises(llm.LLMOfflineError):
        provider.complete_json("t", "an uncached prompt", Answer)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_the_budget_is_enforced(monkeypatch):
    monkeypatch.setattr(llm, "MAX_LLM_CALLS", 2)
    provider = FakeProvider()
    provider.complete_json("t", "one", Answer)
    provider.complete_json("t", "two", Answer)
    with pytest.raises(llm.LLMBudgetExceeded):
        provider.complete_json("t", "three", Answer)


def test_usage_reports_a_hit_rate():
    provider = FakeProvider()
    provider.complete_json("t", "one", Answer)
    provider.complete_json("t", "one", Answer)
    report = llm.usage()
    assert report["llm_calls"] == 1
    assert report["cache_hits"] == 1
    assert report["requests"] == 2
    assert report["cache_hit_rate"] == 0.5


def test_usage_accounts_for_tokens_and_latency():
    """The fake provider never calls record_usage, so these stay at zero --
    which is the point: only a real network call may add to them."""
    provider = FakeProvider()
    provider.complete_json("t", "one", Answer)
    assert llm.usage()["total_tokens"] == 0

    llm.record_usage(120, 45, 1.5)
    report = llm.usage()
    assert report["prompt_tokens"] == 120
    assert report["completion_tokens"] == 45
    assert report["total_tokens"] == 165
    assert report["api_seconds"] == 1.5


def test_a_cache_hit_adds_no_tokens():
    provider = FakeProvider()
    provider.complete_json("t", "one", Answer)
    llm.record_usage(100, 20, 0.8)
    before = llm.usage()

    provider.complete_json("t", "one", Answer)   # cache hit
    after = llm.usage()
    assert after["total_tokens"] == before["total_tokens"]
    assert after["api_seconds"] == before["api_seconds"]
    assert after["cache_hits"] == before["cache_hits"] + 1


# ---------------------------------------------------------------------------
# Validation and retry
# ---------------------------------------------------------------------------

def test_invalid_json_is_retried_once_with_the_error_attached():
    provider = FakeProvider(responses=[
        '{"verdict": "same_person"}',                      # missing confidence
        '{"verdict": "same_person", "confidence": 0.8}',   # corrected
    ])
    result = provider.complete_json("t", "prompt", Answer)
    assert result["confidence"] == 0.8
    assert len(provider.prompts) == 2
    assert "Validation errors" in provider.prompts[1]
    assert "confidence" in provider.prompts[1]


def test_two_invalid_responses_fail_loudly():
    provider = FakeProvider(responses=['{"nope": 1}', '{"still": "wrong"}'])
    with pytest.raises(llm.LLMError) as excinfo:
        provider.complete_json("t", "prompt", Answer)
    assert "twice" in str(excinfo.value)


def test_a_failed_call_is_not_cached():
    provider = FakeProvider(responses=['{"nope": 1}', '{"still": "wrong"}'])
    with pytest.raises(llm.LLMError):
        provider.complete_json("t", "prompt", Answer)
    assert not list(cache.CACHE_DIR.rglob("*.json"))


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def test_gemini_is_a_real_provider_now():
    """It was a stub; it is not any more. The interface is the same one Groq
    implements, which is the whole point of having had an abstraction."""
    from backend.app.llm.gemini_provider import GeminiProvider

    assert issubclass(GeminiProvider, llm.LLMProvider)
    assert GeminiProvider.name == "gemini"
    assert GeminiProvider._generate is not llm.LLMProvider._generate


def test_a_provider_can_be_built_without_a_key(monkeypatch):
    """The deployed demo has no credentials and must still start.

    A cache hit returns from the base class before any provider code runs, so
    demanding a key at construction made a fully-cached run impossible to boot.
    The key is required at the moment a call actually leaves the process, and
    not before.
    """
    monkeypatch.setattr(llm.config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", None)
    from backend.app.llm.gemini_provider import GeminiProvider
    from backend.app.llm.groq_provider import GroqProvider

    assert GeminiProvider().name == "gemini"
    assert GroqProvider().name == "groq"


def test_a_keyless_provider_fails_only_when_it_has_to_call(monkeypatch):
    monkeypatch.setattr(llm.config, "GEMINI_API_KEY", None)
    from backend.app.llm.gemini_provider import GeminiProvider

    provider = GeminiProvider()
    with pytest.raises(llm.LLMError) as excinfo:
        _ = provider._client
    assert "GOOGLE_API_KEY" in str(excinfo.value)


def test_a_keyless_provider_still_serves_a_cache_hit(monkeypatch):
    """The property that matters for deployment, asserted directly."""
    monkeypatch.setattr(llm.config, "GROQ_API_KEY", None)
    from backend.app.llm.groq_provider import GroqProvider

    warm = FakeProvider()
    warm.name, warm.model = "groq", llm.config.GROQ_MODEL
    warm.complete_json("t", "prompt", Answer)

    cold = GroqProvider()          # no key at all
    assert cold.complete_json("t", "prompt", Answer)["verdict"] == "same_person"
    assert llm.calls_made() == 1, "the keyless provider must not have called out"


def test_gemini_schema_drops_what_google_rejects():
    """Pydantic emits $defs/$ref and `title`; Gemini's validator does not take
    them. The schema is derived from the pydantic model rather than maintained
    by hand, so the two cannot drift."""
    from backend.app.llm.gemini_provider import gemini_schema

    class Inner(BaseModel):
        x: str

    class Outer(BaseModel):
        items: list[Inner]

    schema = gemini_schema(Outer)
    text = json.dumps(schema)
    assert "$defs" not in text and "$ref" not in text and '"title"' not in text
    assert schema["properties"]["items"]["items"]["properties"]["x"]["type"] == "string"


def test_provider_resolution_switches_on_config(monkeypatch):
    monkeypatch.setattr(llm.config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm.config, "GEMINI_API_KEY", None)
    assert llm.get_provider().name == "gemini"

    monkeypatch.setattr(llm.config, "LLM_PROVIDER", "groq")
    assert llm.get_provider().name == "groq"


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        llm.get_provider("wishful")
