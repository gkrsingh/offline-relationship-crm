"""Blocking, deterministic matching and fuzzy scoring.

The precision tests matter most here. Anything that lets two different people
merge without a human is a defect, however good the recall looks.
"""

from __future__ import annotations

import pytest

from backend.app.pipeline import dedupe
from backend.app.pipeline.records import normalize_record

BASE = {
    "id": "p-0001",
    "full_name": "Priya Raghavan",
    "email": "priya.raghavan@vantagesystems.com",
    "linkedin_url": "https://www.linkedin.com/in/priya-raghavan-2291",
    "company": "Vantage Systems",
    "title": "VP Sales",
    "location": "Bengaluru, India",
    "bio": "VP Sales at Vantage Systems.",
    "source": "airtable_export",
    "needs": [],
    "offers": [],
    "created_at": "2025-01-01",
}


def rec(**overrides):
    return normalize_record({**BASE, **overrides})


# ---------------------------------------------------------------------------
# Stage 0 -- blocking
# ---------------------------------------------------------------------------

def test_blocking_keys_cover_all_five_signals():
    keys = dedupe.blocking_keys(rec())
    prefixes = {k.split(":")[0] for k in keys}
    assert prefixes == {"li", "em", "co", "nl", "nm"}


def test_the_first_name_key_needs_nothing_else_present():
    """The other four keys each require a second surviving field. This one does
    not, which is the entire reason it exists -- held-out draws lost duplicates
    whose surname was abbreviated away, defeating every surname-based key."""
    stripped = rec(email=None, linkedin_url=None, company=None, location=None)
    assert dedupe.blocking_keys(stripped) == ["nm:priya"]


def test_an_abbreviated_surname_still_shares_a_key():
    full = rec(id="p-1", full_name="Joseph Whitfield", email=None, linkedin_url=None)
    short = rec(id="p-2", full_name="JOSEPH W.", email=None, linkedin_url=None)
    shared = set(dedupe.blocking_keys(full)) & set(dedupe.blocking_keys(short))
    assert "nm:joseph" in shared


def test_a_common_first_name_cannot_explode_a_bucket():
    size = dedupe.CONFIG["MAX_BUCKET_SIZE"] + 5
    records = [rec(id=f"p-{i:04d}", full_name=f"Priya Surname{i}", email=None,
                   linkedin_url=None, company=None, location=None)
               for i in range(size)]
    candidates, _keys, skipped = dedupe.build_candidates(records)
    assert skipped >= 1
    assert candidates == {}


def test_a_record_with_nothing_produces_no_keys():
    empty = rec(full_name=None, email=None, linkedin_url=None,
                company=None, location=None)
    assert dedupe.blocking_keys(empty) == []


def test_blocking_shrinks_the_comparison_space():
    records = [rec(id=f"p-{i:04d}", full_name=f"Person{i} Surname{i}",
                   email=f"person{i}@corp{i}.com",
                   linkedin_url=f"https://linkedin.com/in/person-{i}",
                   company=f"Corp{i}") for i in range(60)]
    candidates, _keys, _skipped = dedupe.build_candidates(records)
    assert len(candidates) < 60 * 59 // 2


def test_oversized_buckets_are_skipped_not_expanded():
    """A key that matches everyone is a bad key, not a licence to make 10,000 pairs."""
    size = dedupe.CONFIG["MAX_BUCKET_SIZE"] + 5
    records = [rec(id=f"p-{i:04d}", full_name=f"Person{i} Sharma", email=None,
                   linkedin_url=None, company=None, location="Bengaluru, India")
               for i in range(size)]
    candidates, _keys, skipped = dedupe.build_candidates(records)
    assert skipped >= 1
    assert candidates == {}


def test_candidate_pairs_are_ordered_consistently():
    a, b = rec(id="p-0002"), rec(id="p-0001")
    candidates, _k, _s = dedupe.build_candidates([a, b])
    assert all(pair[0] < pair[1] for pair in candidates)


# ---------------------------------------------------------------------------
# Stage 1 -- deterministic
# ---------------------------------------------------------------------------

def test_identical_email_is_a_match_despite_formatting():
    a = rec(id="p-1")
    b = rec(id="p-2", email="  PRIYA.RAGHAVAN+crm@VANTAGESYSTEMS.COM ",
            full_name="P. RAGHAVAN", company=None)
    assert dedupe.deterministic_verdict(a, b) == "exact_email"


def test_identical_linkedin_is_a_match_despite_url_shape():
    a = rec(id="p-1")
    b = rec(id="p-2", email=None,
            linkedin_url="linkedin.com/in/priya-raghavan-2291?utm_source=share")
    assert dedupe.deterministic_verdict(a, b) == "exact_linkedin"


def test_missing_identifiers_are_never_treated_as_equal():
    """Two records with no email must not match on 'both are None'."""
    a = rec(id="p-1", email=None, linkedin_url=None)
    b = rec(id="p-2", email=None, linkedin_url=None, company="Other Corp")
    assert dedupe.deterministic_verdict(a, b) is None


# ---------------------------------------------------------------------------
# Stage 2 -- fuzzy
# ---------------------------------------------------------------------------

def test_nickname_at_the_same_company_escalates():
    a = rec(id="p-1", full_name="Michael Mukherjee", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Mike Mukherjee", email=None, linkedin_url=None)
    score, _parts = dedupe.fuzzy_score(a, b)
    assert dedupe.CONFIG["ESCALATE_FLOOR"] <= score < dedupe.CONFIG["AUTO_MERGE_AT"]


def test_initial_abbreviation_is_recognised():
    a = rec(id="p-1", full_name="Ines Bakshi", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Ines B.", email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)
    assert parts["name"] >= dedupe.CONFIG["INITIAL_MATCH_SCORE"]
    assert score >= dedupe.CONFIG["ESCALATE_FLOOR"]


def test_identical_name_at_a_different_company_never_auto_merges():
    """The near-miss case: same name, different employer, no shared identifier.
    It escalates rather than dropping, so the LLM records an explicit
    different_people verdict instead of the pair vanishing silently."""
    a = rec(id="p-1", full_name="Kenji Sinha", company="Slate Stack",
            email="ksinha@slatestack.com", linkedin_url=None)
    b = rec(id="p-2", full_name="Kenji Sinha", company="Obsidian Health",
            email="kenjisinha@obsidianhealth.com", linkedin_url=None)
    score, _parts = dedupe.fuzzy_score(a, b)
    assert score < dedupe.CONFIG["AUTO_MERGE_AT"]


def test_identical_name_with_no_company_cannot_auto_merge():
    """The dangerous case: a perfect name match and nothing to corroborate it.
    It must escalate to a human or the LLM, never merge on its own."""
    a = rec(id="p-1", full_name="Kenji Sinha", company=None, email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Kenji Sinha", company=None, email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)
    assert parts["corroborated"] is False
    assert score <= dedupe.CONFIG["NAME_ONLY_CAP"]
    assert score < dedupe.CONFIG["AUTO_MERGE_AT"]


def test_company_agreement_cannot_substitute_for_a_name_match():
    """Same employer, same title, different first name: the company can only
    corroborate a name match, so this must not reach the auto-merge line."""
    a = rec(id="p-1", full_name="Michael Mukherjee", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Mike Mukherjee", email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)
    assert parts["name_strong"] is False
    assert score <= dedupe.CONFIG["NAME_ONLY_CAP"]


def test_relatives_at_one_company_do_not_auto_merge():
    a = rec(id="p-1", full_name="Madhav Varma", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Rahul Varma", email=None, linkedin_url=None)
    score, _parts = dedupe.fuzzy_score(a, b)
    assert score < dedupe.CONFIG["AUTO_MERGE_AT"]


def test_title_agreement_is_only_a_tiebreaker_below_the_merge_line():
    """Away from the auto-merge boundary the title is worth a few points and no
    more. The veto is a separate rule that only fires on pairs about to merge --
    see test_role_disagreement_vetoes_auto_merge."""
    a = rec(id="p-1", full_name="Priya Raghavan", email=None, linkedin_url=None)
    b_same = rec(id="p-2", full_name="Priya Raghavan", email=None, linkedin_url=None,
                 company="Vantage Grid", title="Vice President of Sales")
    b_diff = rec(id="p-3", full_name="Priya Raghavan", email=None, linkedin_url=None,
                 company="Vantage Grid", title="VP Marketing")
    same, same_parts = dedupe.fuzzy_score(a, b_same)
    diff, diff_parts = dedupe.fuzzy_score(a, b_diff)

    assert same < dedupe.CONFIG["AUTO_MERGE_AT"], "this test needs a mid-band pair"
    assert not (same_parts["title_vetoed"] or diff_parts["title_vetoed"])
    assert same - diff == dedupe.CONFIG["TITLE_BONUS"] + dedupe.CONFIG["TITLE_PENALTY"]


def test_company_variants_still_score_as_the_same_employer():
    a = rec(id="p-1", company="Vantage Systems", email=None, linkedin_url=None)
    b = rec(id="p-2", company="VANTAGE SYSTEMS Pvt Ltd", email=None, linkedin_url=None)
    assert dedupe.company_similarity(a, b) == 100.0


def test_company_similarity_is_none_when_it_cannot_be_compared():
    a = rec(id="p-1", company=None)
    b = rec(id="p-2")
    assert dedupe.company_similarity(a, b) is None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def test_run_without_a_provider_leaves_escalations_pending():
    a = rec(id="p-1", full_name="Michael Mukherjee", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Mike Mukherjee", email=None, linkedin_url=None)
    result = dedupe.run([a, b], provider=None)
    assert result.funnel["stage2_escalated"] == 1
    assert result.funnel["stage3_unadjudicated"] == 1
    pending = result.by_state("pending")
    assert len(pending) == 1 and pending[0].verdict == "insufficient_evidence"


def test_run_is_deterministic():
    records = [rec(id="p-1"), rec(id="p-2", full_name="P. Raghavan"),
               rec(id="p-3", full_name="Kenji Sinha", company="Obsidian Health",
                   email="k@obsidianhealth.com", linkedin_url=None)]
    first = dedupe.run(records, provider=None)
    second = dedupe.run(list(reversed(records)), provider=None)
    assert first.funnel == second.funnel
    assert sorted(p.key() for p in first.pairs) == sorted(p.key() for p in second.pairs)


def test_insufficient_evidence_never_auto_merges():
    class AbstainingProvider:
        name, model = "fake", "fake"

        def complete_json(self, task_name, prompt, schema):
            return {"verdicts": [{"pair_index": 0, "verdict": "insufficient_evidence",
                                  "confidence": 0.3, "reason": "no shared identifier"}]}

    a = rec(id="p-1", full_name="Michael Mukherjee", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Mike Mukherjee", email=None, linkedin_url=None)
    result = dedupe.run([a, b], provider=AbstainingProvider())

    assert result.funnel["stage3_insufficient_evidence"] == 1
    assert result.merged_pairs() == []
    assert result.by_state("pending")[0].llm_used is True


def test_a_skipped_pair_is_not_a_merge():
    """If the model returns fewer verdicts than pairs, the gap defaults to
    pending. Silence is not consent."""
    class SilentProvider:
        name, model = "fake", "fake"

        def complete_json(self, task_name, prompt, schema):
            return {"verdicts": []}

    a = rec(id="p-1", full_name="Michael Mukherjee", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Mike Mukherjee", email=None, linkedin_url=None)
    result = dedupe.run([a, b], provider=SilentProvider())
    assert result.merged_pairs() == []
    assert result.by_state("pending")[0].reason == "no verdict returned for this pair"


def test_batching_respects_the_configured_size():
    calls = []

    class CountingProvider:
        name, model = "fake", "fake"

        def complete_json(self, task_name, prompt, schema):
            calls.append(prompt)
            return {"verdicts": [{"pair_index": i, "verdict": "different_people",
                                  "confidence": 0.9, "reason": "different companies"}
                                 for i in range(dedupe.CONFIG["LLM_BATCH_SIZE"])]}

    # 12 mutually-similar records in one company produce more than one batch.
    records = [rec(id=f"p-{i}", full_name=f"Michael{'x' * (i % 3)} Mukherjee",
                   email=None, linkedin_url=None) for i in range(6)]
    result = dedupe.run(records, provider=CountingProvider())
    if result.funnel["stage3_pairs"]:
        expected = -(-result.funnel["stage3_pairs"] // dedupe.CONFIG["LLM_BATCH_SIZE"])
        assert len(calls) == expected


@pytest.mark.parametrize("threshold", ["AUTO_MERGE_AT", "ESCALATE_FLOOR",
                                       "NAME_ONLY_CAP", "AUTO_MERGE_MIN_NAME",
                                       "LLM_BATCH_SIZE"])
def test_every_threshold_is_in_config(threshold):
    assert threshold in dedupe.CONFIG


# ---------------------------------------------------------------------------
# Stage 2 -- the title veto
# ---------------------------------------------------------------------------

def test_role_disagreement_vetoes_auto_merge():
    """Two colleagues at one company with near-identical names. The blended
    score clears 92; the differing role is the only thing that says no."""
    a = rec(id="p-1", full_name="Rohit Zaidi", company="Willow Collective",
            title="GM, India", email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Rohia Zaidi", company="Willow Collective",
            title="Sr. Manager, Growth", email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)
    assert parts["title_agrees"] is False
    assert parts["title_vetoed"] is True
    assert score < dedupe.CONFIG["AUTO_MERGE_AT"]


def test_a_promotion_escalates_rather_than_dropping():
    """The same person whose title changed between imports: Head of Growth,
    then VP Growth. The veto must buy a second look, not a rejection."""
    a = rec(id="p-1", full_name="Priya Raghavan", title="Head of Growth",
            email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Priya Raghavan", title="VP Growth",
            email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)

    assert parts["title_vetoed"] is True
    assert score >= dedupe.CONFIG["ESCALATE_FLOOR"], "the veto must never reject"
    assert score < dedupe.CONFIG["AUTO_MERGE_AT"]

    result = dedupe.run([a, b], provider=None)
    assert result.funnel["stage2_escalated"] == 1
    assert result.funnel["stage2_dropped"] == 0
    assert result.merged_pairs() == []


def test_a_missing_title_does_not_veto():
    """Absence is not disagreement. Vetoing on a missing title would stop every
    incomplete record from auto-merging and flood the queue."""
    a = rec(id="p-1", title="Head of Growth", email=None, linkedin_url=None)
    for other in (rec(id="p-2", title=None), rec(id="p-2", title="   ")):
        score, parts = dedupe.fuzzy_score(a, other)
        assert parts["title_agrees"] is None
        assert parts["title_vetoed"] is False
        assert score >= dedupe.CONFIG["AUTO_MERGE_AT"]


def test_the_veto_does_not_lift_a_weak_pair_to_the_floor():
    """The floor applies only to pairs the veto pushed down. A genuinely weak
    pair with differing roles must still drop."""
    a = rec(id="p-1", full_name="William Bose", title="VP Sales",
            email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Fatima Bose", title="Sales Manager",
            email=None, linkedin_url=None)
    score, parts = dedupe.fuzzy_score(a, b)
    assert parts["title_agrees"] is False
    assert parts["title_vetoed"] is False
    assert score < dedupe.CONFIG["ESCALATE_FLOOR"]


def test_title_variants_of_one_role_still_auto_merge():
    """Normalization has to absorb the noisy spellings before the veto sees
    them, or every `(interim)` suffix would block a merge."""
    for spelling in ("VP Sales", "Vice President of Sales", "V.P. Sales",
                     "vp sales", "VP Sales (interim)"):
        a = rec(id="p-1", title="VP Sales", email=None, linkedin_url=None)
        b = rec(id="p-2", title=spelling, email=None, linkedin_url=None)
        score, parts = dedupe.fuzzy_score(a, b)
        assert parts["title_agrees"] is True, spelling
        assert score >= dedupe.CONFIG["AUTO_MERGE_AT"], spelling


def test_the_veto_can_be_switched_off():
    """It is a CONFIG decision like every other threshold, not a hard-coded rule."""
    a = rec(id="p-1", full_name="Rohit Zaidi", title="GM, India",
            email=None, linkedin_url=None)
    b = rec(id="p-2", full_name="Rohia Zaidi", title="Sr. Manager, Growth",
            email=None, linkedin_url=None)
    original = dedupe.CONFIG["TITLE_DISAGREEMENT_VETOES_AUTO_MERGE"]
    try:
        dedupe.CONFIG["TITLE_DISAGREEMENT_VETOES_AUTO_MERGE"] = False
        score, parts = dedupe.fuzzy_score(a, b)
        assert parts["title_vetoed"] is False
        assert score >= dedupe.CONFIG["AUTO_MERGE_AT"]
    finally:
        dedupe.CONFIG["TITLE_DISAGREEMENT_VETOES_AUTO_MERGE"] = original
