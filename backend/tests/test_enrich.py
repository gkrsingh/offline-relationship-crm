"""AI enrichment: closed enums, `unknown` as a real answer, and checked evidence.

No network. A fake provider returns whatever the test needs, which is enough to
cover everything that matters here -- the enum contract, the refusal to guess,
and the evidence verifier that catches a fabricated quote.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.llm import cache
from backend.app.pipeline import enrich
from backend.app.pipeline.records import normalize_record


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Enrichment now caches per person, so a test must not read or write the
    real cache -- and must not see what the previous test wrote."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "llm")
    yield

BASE = {
    "id": "p-0001",
    "full_name": "Priya Raghavan",
    "email": "priya@vantagesystems.com",
    "linkedin_url": "https://www.linkedin.com/in/priya-raghavan-2291",
    "company": "Vantage Systems",
    "title": "Co-founder & CEO",
    "location": "Bengaluru, India",
    "bio": "Co-founder & CEO at Vantage Systems, a seed-stage B2B SaaS company. "
           "Previously eight years at a large enterprise software company.",
    "source": "airtable_export",
    "needs": ["hiring senior GTM talent"],
    "offers": ["B2B GTM expertise"],
    "created_at": "2024-01-01",
}


def rec(**overrides):
    return normalize_record({**BASE, **overrides})


def answer(person_id="p-0001", **overrides):
    payload = {
        "person_id": person_id,
        "persona": "founder",
        "seniority": "c_level",
        "company_stage": "seed",
        "sector": "b2b_saas",
        "geography": "india",
        "needs": ["hiring senior GTM talent"],
        "offers": ["B2B GTM expertise"],
        "confidence": 0.9,
        "evidence": [{"field": "title", "quote": "Co-founder & CEO", "supports": "persona"}],
    }
    payload.update(overrides)
    return payload


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    def complete_json(self, task_name, prompt, schema):
        self.calls += 1
        payload = self.batches[min(self.calls - 1, len(self.batches) - 1)]
        return {"people": payload}


# ---------------------------------------------------------------------------
# The enum contract
# ---------------------------------------------------------------------------

def test_every_enum_admits_unknown():
    """`unknown` is not a fallback bolted on -- it is a member of every enum."""
    for annotation in (enrich.Persona, enrich.Seniority, enrich.CompanyStage,
                       enrich.Sector, enrich.Geography):
        assert "unknown" in annotation.__args__


def test_the_allowed_personas_are_exactly_the_agreed_ones():
    assert set(enrich.Persona.__args__) == {
        "founder", "operator", "investor", "service_provider", "ic", "unknown"}


def test_a_value_outside_the_enum_is_rejected():
    with pytest.raises(ValidationError):
        enrich.PersonEnrichment(**answer(persona="startup founder"))


def test_a_fully_unknown_record_is_valid():
    model = enrich.PersonEnrichment(**answer(
        persona="unknown", seniority="unknown", company_stage="unknown",
        sector="unknown", geography="unknown", needs=[], offers=[],
        confidence=0.1, evidence=[]))
    assert model.persona == "unknown"


def test_confidence_is_bounded():
    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            enrich.PersonEnrichment(**answer(confidence=bad))


def test_evidence_may_only_cite_a_real_field():
    with pytest.raises(ValidationError):
        enrich.PersonEnrichment(**answer(
            evidence=[{"field": "vibes", "quote": "x", "supports": "persona"}]))


# ---------------------------------------------------------------------------
# Evidence verification
# ---------------------------------------------------------------------------

def test_a_real_quote_verifies():
    check = enrich.verify_evidence(rec(), [
        {"field": "title", "quote": "Co-founder & CEO", "supports": "persona"},
        {"field": "bio", "quote": "seed-stage B2B SaaS", "supports": "company_stage"},
    ])
    assert check.ok and check.verified == 2


def test_a_fabricated_quote_is_caught():
    """The classification may be right and the justification still invented."""
    check = enrich.verify_evidence(rec(), [
        {"field": "bio", "quote": "raised a Series B last year", "supports": "company_stage"},
    ])
    assert not check.ok
    assert check.unverified[0]["why"] == "quote does not appear in that field"


def test_citing_an_empty_field_is_caught():
    check = enrich.verify_evidence(rec(bio=None), [
        {"field": "bio", "quote": "seed-stage B2B SaaS", "supports": "company_stage"},
    ])
    assert not check.ok
    assert "empty" in check.unverified[0]["why"]


def test_verification_tolerates_case_accents_and_spacing():
    check = enrich.verify_evidence(rec(), [
        {"field": "title", "quote": "co-founder  &  CEO", "supports": "persona"},
    ])
    assert check.ok


def test_a_trivial_quote_is_not_evidence():
    check = enrich.verify_evidence(rec(), [
        {"field": "bio", "quote": "a", "supports": "persona"},
    ])
    assert not check.ok
    assert "too short" in check.unverified[0]["why"]


def test_no_evidence_verifies_vacuously():
    check = enrich.verify_evidence(rec(), [])
    assert check.ok and check.rate == 1.0


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def test_a_record_the_model_skips_becomes_unknown_not_a_guess():
    """Silence is not a licence to invent. A dropped record is stored as an
    explicit unknown at zero confidence."""
    provider = FakeProvider([[]])
    result = enrich.run([rec()], provider)

    assert result.missing == ["p-0001"]
    person = result.people[0]
    assert person.persona == "unknown"
    assert person.confidence == 0.0
    assert person.low_confidence


def test_every_record_gets_a_row_even_when_the_model_is_partial():
    records = [rec(id=f"p-{i:04d}") for i in range(1, 6)]
    provider = FakeProvider([[answer("p-0001"), answer("p-0003")]])
    result = enrich.run(records, provider)

    assert len(result.people) == len(records)
    assert {p.person_id for p in result.people} == {r.id for r in records}
    assert sorted(result.missing) == ["p-0002", "p-0004", "p-0005"]


def test_batching_respects_the_configured_size():
    records = [rec(id=f"p-{i:04d}") for i in range(1, 14)]
    provider = FakeProvider([[answer(r.id) for r in records]])
    result = enrich.run(records, provider, batch_size=5)
    assert provider.calls == 3
    assert result.funnel["batches"] == 3


def test_bad_evidence_is_recorded_not_discarded():
    provider = FakeProvider([[answer(evidence=[
        {"field": "title", "quote": "Co-founder & CEO", "supports": "persona"},
        {"field": "bio", "quote": "raised a Series B", "supports": "company_stage"},
    ])]])
    result = enrich.run([rec()], provider)
    person = result.people[0]

    assert person.evidence_total == 2
    assert person.evidence_verified == 1
    assert len(person.evidence_unverified) == 1
    assert result.funnel["records_with_bad_evidence"] == 1
    assert len(person.evidence) == 2, "the raw claim is kept for inspection"


def test_low_confidence_is_flagged():
    provider = FakeProvider([[answer(confidence=0.2)]])
    result = enrich.run([rec()], provider)
    assert result.people[0].low_confidence
    assert result.funnel["low_confidence"] == 1


def test_unknown_personas_are_counted():
    provider = FakeProvider([[answer(persona="unknown")]])
    result = enrich.run([rec()], provider)
    assert result.funnel["unknown_persona"] == 1


def test_batching_is_deterministic_regardless_of_input_order():
    records = [rec(id=f"p-{i:04d}") for i in range(1, 8)]
    payload = [answer(r.id) for r in records]

    forward = FakeProvider([payload])
    backward = FakeProvider([payload])
    a = enrich.run(records, forward, batch_size=3)
    b = enrich.run(list(reversed(records)), backward, batch_size=3)

    assert [p.person_id for p in a.people] == [p.person_id for p in b.people]
    assert a.funnel == b.funnel


def test_the_prompt_states_the_rules_that_matter():
    prompt = enrich.build_prompt([rec()])
    assert "unknown" in prompt
    assert "never penalised" in prompt.lower() or "never penalized" in prompt.lower()
    assert "verbatim" in prompt
    assert "p-0001" in prompt


def test_the_prompt_shows_empty_fields_as_empty():
    prompt = enrich.build_prompt([rec(bio=None, company=None)])
    assert "bio: (empty)" in prompt
    assert "company: (empty)" in prompt
