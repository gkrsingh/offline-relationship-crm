"""The introduction engine: complementarity, reciprocity, and the safety filters.

Embeddings are faked with hand-built unit vectors so the scoring maths is tested
directly rather than through a model. The filters are tested for the thing that
actually matters about them: that they fire before anything is suggested, and
that each one is attributable.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.app.pipeline import intros
from backend.app.pipeline.records import normalize_record

BASE = {
    "id": "p-0001",
    "full_name": "Priya Raghavan",
    "email": "priya@vantagesystems.com",
    "linkedin_url": "https://www.linkedin.com/in/priya-raghavan-2291",
    "company": "Vantage Systems",
    "title": "Co-founder & CEO",
    "location": "Bengaluru, India",
    "bio": "Co-founder at Vantage Systems, a seed-stage B2B SaaS company.",
    "source": "airtable_export",
    "needs": ["hiring senior GTM talent"],
    "offers": ["fundraising experience"],
    "created_at": "2024-01-01",
}

DIM = 8


def rec(**overrides):
    return normalize_record({**BASE, **overrides})


def unit(index: int, tilt: float = 0.0, other: int = 0) -> np.ndarray:
    """A basis vector, optionally rotated toward another basis vector."""
    vector = np.zeros(DIM, dtype=np.float32)
    vector[index] = 1.0
    if tilt:
        vector[other] = tilt
    return vector / np.linalg.norm(vector)


def vectors(person_id, need_axes=(), offer_axes=(), profile_axis=7,
            needs=None, offers=None):
    need_vectors = (np.vstack([unit(a) for a in need_axes]) if need_axes
                    else np.empty((0, DIM), dtype=np.float32))
    offer_vectors = (np.vstack([unit(a) for a in offer_axes]) if offer_axes
                     else np.empty((0, DIM), dtype=np.float32))
    return intros.PersonVectors(
        person_id=person_id,
        needs=needs or [f"need {a}" for a in need_axes],
        offers=offers or [f"offer {a}" for a in offer_axes],
        need_vectors=need_vectors,
        offer_vectors=offer_vectors,
        profile_vector=unit(profile_axis),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_a_perfect_one_way_match_scores_but_earns_no_bonus():
    a = vectors("p-1", need_axes=(0,))
    b = vectors("p-2", offer_axes=(0,))
    suggestion = intros.score_pair(a, b)

    assert suggestion is not None
    assert suggestion.complementarity == pytest.approx(1.0)
    assert suggestion.reverse_complementarity == pytest.approx(0.0)
    assert suggestion.reciprocal is False


def test_a_two_way_match_outscores_a_one_way_one():
    one_way = intros.score_pair(vectors("p-1", need_axes=(0,)),
                                vectors("p-2", offer_axes=(0,)))
    two_way = intros.score_pair(vectors("p-3", need_axes=(0,), offer_axes=(1,)),
                                vectors("p-4", need_axes=(1,), offer_axes=(0,)))
    assert two_way.reciprocal is True
    assert two_way.score > one_way.score


def test_the_reciprocity_bonus_is_worth_its_configured_weight():
    two_way = intros.score_pair(vectors("p-3", need_axes=(0,), offer_axes=(1,)),
                                vectors("p-4", need_axes=(1,), offer_axes=(0,)))
    expected = (intros.CONFIG["PRIMARY_DIRECTION_WEIGHT"]
                + intros.CONFIG["SECOND_DIRECTION_WEIGHT"]
                + intros.CONFIG["SIMILARITY_WEIGHT"]
                + intros.CONFIG["RECIPROCITY_BONUS"])
    assert two_way.score == pytest.approx(expected, abs=0.01)


def test_similarity_alone_cannot_produce_a_match():
    """Two identical profiles with nothing to trade must not be suggested."""
    a = vectors("p-1", need_axes=(), offer_axes=(), profile_axis=7)
    b = vectors("p-2", need_axes=(), offer_axes=(), profile_axis=7)
    assert intros.score_pair(a, b) is None


def test_similarity_is_weighted_below_complementarity():
    assert (intros.CONFIG["SIMILARITY_WEIGHT"]
            < intros.CONFIG["PRIMARY_DIRECTION_WEIGHT"])
    assert (intros.CONFIG["SIMILARITY_WEIGHT"]
            < intros.CONFIG["PRIMARY_DIRECTION_WEIGHT"]
            + intros.CONFIG["SECOND_DIRECTION_WEIGHT"])


def test_an_unrelated_pair_is_rejected():
    a = vectors("p-1", need_axes=(0,))
    b = vectors("p-2", offer_axes=(3,))
    assert intros.score_pair(a, b) is None


def test_the_specific_matched_phrases_are_recorded():
    a = vectors("p-1", need_axes=(0, 2), needs=["a CFO", "a design partner"])
    b = vectors("p-2", offer_axes=(5, 2), offers=["SOC 2 work", "design partnerships"])
    suggestion = intros.score_pair(a, b)
    assert suggestion.matched_need == "a design partner"
    assert suggestion.matched_offer == "design partnerships"


def test_the_pair_is_oriented_so_a_is_the_one_being_helped():
    weak = vectors("p-1", need_axes=(4,))          # nothing matches
    strong = vectors("p-2", need_axes=(0,), offer_axes=(4,))
    suggestion = intros.score_pair(weak, strong)
    assert suggestion.a_id == "p-1"                # A's need is the one answered
    assert suggestion.b_id == "p-2"


def test_a_pair_is_one_pair_whichever_way_round_it_is_built():
    a = vectors("p-1", need_axes=(0,), offer_axes=(1,))
    b = vectors("p-2", need_axes=(1,), offer_axes=(0,))
    assert intros.score_pair(a, b).key() == intros.score_pair(b, a).key()


def test_scoring_is_deterministic():
    a = vectors("p-1", need_axes=(0,), offer_axes=(1,))
    b = vectors("p-2", need_axes=(1,), offer_axes=(0,))
    assert intros.score_pair(a, b).score == intros.score_pair(a, b).score


# ---------------------------------------------------------------------------
# Safety filters
# ---------------------------------------------------------------------------

def test_colleagues_are_never_suggested():
    a, b = rec(id="p-1"), rec(id="p-2", full_name="Arjun Mehta")
    assert intros.same_company(a, b) is True

    result = intros.build_suggestions(
        [a, b], {"p-1": vectors("p-1", need_axes=(0,)),
                 "p-2": vectors("p-2", offer_axes=(0,))})
    assert result.suggestions == []
    assert result.rejected["same_company"] == 1


def test_company_suffix_variants_still_count_as_the_same_company():
    a = rec(id="p-1", company="Vantage Systems")
    b = rec(id="p-2", company="VANTAGE SYSTEMS Pvt Ltd")
    assert intros.same_company(a, b) is True


@pytest.mark.parametrize("stage_b,expected", [
    ("seed", True),           # same stage
    ("series_a", True),       # adjacent
    ("growth", False),        # far apart: more useful than threatening
])
def test_competitor_detection_is_narrow(stage_b, expected):
    a = {"persona": "founder", "sector": "fintech", "company_stage": "seed"}
    b = {"persona": "founder", "sector": "fintech", "company_stage": stage_b}
    assert intros.are_competitors(a, b) is expected


def test_competitors_needs_both_to_be_founders():
    a = {"persona": "founder", "sector": "fintech", "company_stage": "seed"}
    b = {"persona": "operator", "sector": "fintech", "company_stage": "seed"}
    assert intros.are_competitors(a, b) is False


def test_an_unknown_sector_never_makes_two_people_competitors():
    for sector in ("unknown", "other", None):
        a = {"persona": "founder", "sector": sector, "company_stage": "seed"}
        b = {"persona": "founder", "sector": sector, "company_stage": "seed"}
        assert intros.are_competitors(a, b) is False


def test_missing_enrichment_never_asserts_competition():
    assert intros.are_competitors(None, {"persona": "founder"}) is False
    assert intros.are_competitors({"persona": "founder"}, None) is False


def test_an_unactionable_record_is_never_offered_to_anyone():
    """No email means the intro cannot be sent, so it should not be proposed."""
    thin = rec(id="p-2", email=None, company=None, bio=None, needs=[], offers=[])
    assert intros.actionable(thin, vectors("p-2")) is False


def test_a_blocked_pair_is_never_suggested():
    a = rec(id="p-1", company="Alpha Works")
    b = rec(id="p-2", company="Beta Works", full_name="Arjun Mehta",
            email="arjun@betaworks.com")
    vecs = {"p-1": vectors("p-1", need_axes=(0,)),
            "p-2": vectors("p-2", offer_axes=(0,))}

    allowed = intros.build_suggestions([a, b], vecs)
    assert len(allowed.suggestions) == 1

    blocked = intros.build_suggestions([a, b], vecs, blocked=[("p-2", "p-1")])
    assert blocked.suggestions == []
    assert blocked.rejected["blocked_pair"] == 1


def test_an_existing_introduction_is_not_repeated():
    a = rec(id="p-1", company="Alpha Works")
    b = rec(id="p-2", company="Beta Works", full_name="Arjun Mehta",
            email="arjun@betaworks.com")
    result = intros.build_suggestions(
        [a, b], {"p-1": vectors("p-1", need_axes=(0,)),
                 "p-2": vectors("p-2", offer_axes=(0,))},
        introduced=[("p-1", "p-2")])
    assert result.suggestions == []
    assert result.rejected["already_introduced"] == 1


def test_every_rejection_is_attributed_to_a_named_filter():
    result = intros.build_suggestions([rec(id="p-1")], {"p-1": vectors("p-1")})
    assert set(result.rejected) == set(intros.FILTERS)


# ---------------------------------------------------------------------------
# Top N
# ---------------------------------------------------------------------------

def test_a_hub_person_cannot_flood_the_queue():
    """Six people all want the one CFO. A cap enforced on only one side would
    put that CFO in six suggestions and make the queue useless."""
    records, vecs = [], {}
    hub = rec(id="p-hub", company="Hub Co", needs=["a CFO"], offers=[])
    records.append(hub)
    vecs["p-hub"] = vectors("p-hub", need_axes=(0,))
    for i in range(6):
        pid = f"p-{i}"
        records.append(rec(id=pid, company=f"Co {i}", full_name=f"Person{i} Surname",
                           email=f"p{i}@co{i}.com", needs=[], offers=["CFO work"]))
        vecs[pid] = vectors(pid, offer_axes=(0,))

    result = intros.build_suggestions(records, vecs)
    for person_id in {i for s in result.suggestions for i in (s.a_id, s.b_id)}:
        involved = [s for s in result.suggestions if person_id in (s.a_id, s.b_id)]
        assert len(involved) <= intros.CONFIG["TOP_N_PER_PERSON"], person_id
    assert result.suggestions, "the cap must not reject everything"


def test_the_cap_keeps_the_highest_scoring_pairs():
    """When capacity forces a choice, the better match survives."""
    records, vecs = [], {}
    hub = rec(id="p-hub", company="Hub Co", needs=["a CFO"], offers=[])
    records.append(hub)
    vecs["p-hub"] = vectors("p-hub", need_axes=(0,))
    for i, tilt in enumerate((0.0, 0.15, 0.3, 0.45, 0.6)):
        pid = f"p-{i}"
        records.append(rec(id=pid, company=f"Co {i}", full_name=f"Person{i} Surname",
                           email=f"p{i}@co{i}.com", needs=[], offers=["CFO work"]))
        v = vectors(pid, offer_axes=(0,))
        v.offer_vectors = np.vstack([unit(0, tilt, 3)])
        vecs[pid] = v

    result = intros.build_suggestions(records, vecs)
    kept = {s.b_id if s.a_id == "p-hub" else s.a_id for s in result.suggestions}
    assert "p-0" in kept, "the strongest match must survive the cap"


def test_suggestions_are_deduplicated_across_people():
    records, vecs = [], {}
    for i in range(4):
        pid = f"p-{i}"
        records.append(rec(id=pid, company=f"Co {i}", full_name=f"Person{i} Surname",
                           email=f"p{i}@co{i}.com"))
        vecs[pid] = vectors(pid, need_axes=(0,), offer_axes=(0,))
    result = intros.build_suggestions(records, vecs)
    keys = [s.key() for s in result.suggestions]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Cost estimation and prompt
# ---------------------------------------------------------------------------

def test_the_estimate_scales_with_the_number_of_suggestions():
    few = intros.estimate_tokens([object()] * 10, schema_chars=800)
    many = intros.estimate_tokens([object()] * 100, schema_chars=800)
    assert many["est_total_tokens"] > few["est_total_tokens"]
    assert many["batches"] == -(-100 // intros.CONFIG["LLM_BATCH_SIZE"])


def test_an_empty_run_estimates_nothing():
    assert intros.estimate_tokens([], schema_chars=800)["est_total_tokens"] == 0


def test_the_prompt_forbids_invention_and_caps_the_draft():
    a, b = rec(id="p-1"), rec(id="p-2", full_name="Arjun Mehta")
    suggestion = intros.score_pair(vectors("p-1", need_axes=(0,)),
                                   vectors("p-2", offer_axes=(0,)))
    block = intros.render_pair(1, suggestion, a, b)
    prompt = intros.INTRO_PROMPT.format(schema="{}", pairs=block)

    assert "Do not invent" in prompt
    assert "UNDER 120 WORDS" in prompt
    assert "Nothing you write is\nsent automatically" in prompt
    assert "doing the other a favour" in prompt


def test_a_one_way_pair_is_labelled_as_such_in_the_prompt():
    a, b = rec(id="p-1"), rec(id="p-2", full_name="Arjun Mehta")
    one_way = intros.score_pair(vectors("p-1", need_axes=(0,)),
                                vectors("p-2", offer_axes=(0,)))
    assert "reciprocal: False" in intros.render_pair(1, one_way, a, b)
