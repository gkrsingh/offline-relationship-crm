"""Applicant scoring: the arithmetic, the bands, and the wall between them and the LLM.

The wall is the point of most of these tests. If the model can see the record,
or can introduce a number it was not given, the score stops being deterministic
no matter what the rubric says.
"""

from __future__ import annotations

import pytest

from backend.app.pipeline import scoring

MEMBERS = {"p-0900", "p-0901"}

STRONG_ENRICHMENT = {"persona": "founder", "seniority": "c_level",
                     "company_stage": "series_b"}
WEAK_ENRICHMENT = {"persona": "ic", "seniority": "junior",
                   "company_stage": "pre_seed"}

STRONG_APPLICATION = {
    "building_now": "Running Cipher Cloud, a Series B logistics company. "
                    "Roughly INR 8 crore ARR, 40 in the team.",
    "why_join": "Peers a stage ahead.",
    "contribution": "Happy to help on enterprise GTM.",
    "referred_by": "p-0900",
}
WEAK_APPLICATION = {
    "building_now": "Building something new. Still early days.",
    "why_join": "Want to learn.",
    "contribution": "",
    "referred_by": None,
}


def score(enrichment=STRONG_ENRICHMENT, application=STRONG_APPLICATION,
          completeness=1.0, person_id="p-0001"):
    return scoring.score_applicant(
        person_id=person_id, enrichment=enrichment, application=application,
        completeness=completeness, member_ids=MEMBERS)


# ---------------------------------------------------------------------------
# The rubric
# ---------------------------------------------------------------------------

def test_weights_sum_to_one_hundred():
    assert sum(scoring.CONFIG["WEIGHTS"].values()) == 100


def test_the_five_components_are_the_agreed_ones():
    assert set(scoring.COMPONENTS) == {
        "persona_fit", "seniority", "company_stage",
        "referral_signal", "profile_signal"}


def test_no_component_can_exceed_its_weight():
    for enrichment in (STRONG_ENRICHMENT, WEAK_ENRICHMENT, None):
        breakdown = score(enrichment=enrichment)
        for component in breakdown.components:
            assert 0 <= component.points <= component.max_points, component.name


def test_total_is_the_sum_of_the_components():
    breakdown = score()
    assert breakdown.total == pytest.approx(
        sum(c.points for c in breakdown.components))


def test_the_maximum_possible_score_is_one_hundred():
    breakdown = score()
    ceiling = sum(c.max_points for c in breakdown.components)
    assert ceiling == 100
    assert breakdown.total <= 100


@pytest.mark.parametrize("total,band", [
    (100, "strong"), (75, "strong"), (74.9, "review"),
    (55, "review"), (54.9, "weak"), (0, "weak"),
])
def test_band_boundaries(total, band):
    assert scoring.band_for(total) == band


def test_a_strong_applicant_lands_strong():
    assert score().band == "strong"


def test_a_weak_applicant_lands_weak():
    breakdown = score(enrichment=WEAK_ENRICHMENT, application=WEAK_APPLICATION,
                      completeness=0.4)
    assert breakdown.band == "weak"


def test_scoring_is_deterministic():
    assert score().as_dict() == score().as_dict()


# ---------------------------------------------------------------------------
# Individual signals
# ---------------------------------------------------------------------------

def test_a_founder_outscores_an_ic_on_persona():
    founder = score(enrichment={**STRONG_ENRICHMENT, "persona": "founder"})
    ic = score(enrichment={**STRONG_ENRICHMENT, "persona": "ic"})
    assert founder.component("persona_fit").points > ic.component("persona_fit").points


def test_unknown_is_scored_low_but_not_zero():
    """A record that does not say is not the same as a record that says no."""
    unknown = score(enrichment=None).component("persona_fit")
    ic = score(enrichment={**STRONG_ENRICHMENT, "persona": "ic"}).component("persona_fit")
    assert 0 < unknown.points <= ic.points
    assert "could not be determined" in unknown.signal


def test_a_role_with_no_stage_is_not_punished_for_it():
    """An investor has no startup stage. Scoring that zero would penalise them
    for a question that does not apply."""
    not_applicable = score(enrichment={**STRONG_ENRICHMENT,
                                       "company_stage": "not_applicable"})
    unknown = score(enrichment={**STRONG_ENRICHMENT, "company_stage": "unknown"})
    stage = not_applicable.component("company_stage")
    assert stage.points > unknown.component("company_stage").points
    assert "no company stage applies" in stage.signal


def test_a_referral_from_a_member_beats_an_unresolvable_one_beats_none():
    member = score(application={**STRONG_APPLICATION, "referred_by": "p-0900"})
    stranger = score(application={**STRONG_APPLICATION, "referred_by": "p-9999"})
    cold = score(application={**STRONG_APPLICATION, "referred_by": None})
    points = [b.component("referral_signal").points for b in (member, stranger, cold)]
    assert points[0] > points[1] > points[2]


def test_concrete_traction_earns_profile_signal():
    with_numbers = score()
    without = score(application={**STRONG_APPLICATION,
                                 "building_now": "We are growing quickly."})
    assert (with_numbers.component("profile_signal").points
            > without.component("profile_signal").points)
    assert "no concrete traction" in without.component("profile_signal").signal


def test_completeness_feeds_profile_signal():
    full = score(completeness=1.0).component("profile_signal").points
    thin = score(completeness=0.3).component("profile_signal").points
    assert full > thin


def test_every_component_states_its_basis():
    """A reviewer must be able to see where each number came from."""
    for component in score().components:
        assert component.signal and component.basis


# ---------------------------------------------------------------------------
# The wall between the score and the model
# ---------------------------------------------------------------------------

def test_the_breakdown_carries_no_record_detail():
    breakdown = score()
    payload = str(scoring.explanation_input(breakdown))
    for leak in ("Cipher Cloud", "logistics", "crore", "Happy to help"):
        assert leak not in payload


def test_the_explanation_prompt_follows_the_breakdown_not_the_record():
    """The assertion that matters.

    A breakdown that flatly disagrees with the record is what reaches the
    prompt. If the record could leak in, the model could 'correct' the score;
    because it cannot, the prose has to follow the numbers it was handed.
    """
    record_says_strong = score()
    assert record_says_strong.band == "strong"

    # Same person, a breakdown that says the opposite.
    conflicting = scoring.ScoreBreakdown(
        person_id=record_says_strong.person_id,
        components=[
            scoring.Component("persona_fit", 8, 30, "individual contributor", "x"),
            scoring.Component("seniority", 2, 20, "junior", "x"),
            scoring.Component("company_stage", 5, 20, "pre seed", "x"),
            scoring.Component("referral_signal", 4, 15, "applied cold", "x"),
            scoring.Component("profile_signal", 3, 15, "thin profile", "x"),
        ],
        total=22, band="weak")

    prompt = scoring.build_explanation_prompt(conflicting, schema_json="{}")

    assert "weak" in prompt and "22" in prompt
    assert "strong" not in prompt
    for leak in ("Cipher Cloud", "INR 8 crore", "40 in the team",
                 "Happy to help", "p-0900"):
        assert leak not in prompt, f"record detail {leak!r} reached the prompt"


def test_the_prompt_forbids_inventing_numbers():
    prompt = scoring.build_explanation_prompt(score(), schema_json="{}")
    assert "must appear in the breakdown" in prompt
    assert "do not recompute the total" in prompt.lower()


def test_unsupported_numbers_are_detectable():
    breakdown = score()
    clean = f"Scores {breakdown.total} out of 100, driven by persona fit."
    assert scoring.unsupported_numbers(breakdown, clean) == set()

    invented = "Scores 91 out of 100 after 7 years of experience."
    assert scoring.unsupported_numbers(breakdown, invented)


# ---------------------------------------------------------------------------
# Tone
# ---------------------------------------------------------------------------

def test_a_weak_applicant_gets_a_decline_not_a_review_note():
    breakdown = score(enrichment=WEAK_ENRICHMENT, application=WEAK_APPLICATION,
                      completeness=0.3)
    assert breakdown.kind == "why_not"
    prompt = scoring.build_explanation_prompt(breakdown, schema_json="{}")
    assert "polite decline" in prompt
    assert "not about merit" in prompt or "not merit" in prompt.replace(",", "")


def test_a_strong_applicant_gets_a_review_note():
    breakdown = score()
    assert breakdown.kind == "why"
    prompt = scoring.build_explanation_prompt(breakdown, schema_json="{}")
    assert "polite decline" not in prompt
    assert "weakest one honestly" in prompt


def test_strongest_and_weakest_are_ranked_by_share_not_raw_points():
    """15 out of 15 beats 20 out of 30, even though 20 is the bigger number."""
    breakdown = scoring.ScoreBreakdown(
        person_id="p-1",
        components=[
            scoring.Component("persona_fit", 20, 30, "a", "x"),
            scoring.Component("profile_signal", 15, 15, "b", "x"),
        ],
        total=35, band="weak")
    assert breakdown.strongest(1)[0].name == "profile_signal"
    assert breakdown.weakest(1)[0].name == "persona_fit"
