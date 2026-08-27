"""Completeness scoring and the blocked-reason sentence."""

from __future__ import annotations

import pytest

from backend.app.pipeline.completeness import (
    FIELD_WEIGHTS, describe_gaps, score_completeness,
)

FULL = {
    "email": "priya@vantagesystems.com",
    "linkedin_url": "https://linkedin.com/in/priya",
    "company": "Vantage Systems",
    "title": "VP Sales",
    "bio": "VP Sales at Vantage Systems.",
    "location": "Bengaluru, India",
    "needs": ["hiring"],
    "offers": [],
}


def test_weights_sum_to_one():
    assert round(sum(FIELD_WEIGHTS.values()), 6) == 1.0


def test_a_full_record_scores_one():
    result = score_completeness(FULL)
    assert result.score == 1.0
    assert result.missing == ()
    assert result.blocked is False
    assert result.blocked_reason is None


def test_each_missing_field_costs_its_weight():
    for field, weight in FIELD_WEIGHTS.items():
        record = dict(FULL)
        if field == "needs_offers":
            record["needs"], record["offers"] = [], []
        else:
            record[field] = None
        assert score_completeness(record).score == pytest.approx(1.0 - weight)


def test_needs_or_offers_counts_as_present():
    assert score_completeness({**FULL, "needs": [], "offers": ["GTM"]}).score == 1.0


def test_blank_strings_count_as_missing():
    assert "title" in score_completeness({**FULL, "title": "   "}).missing


def test_missing_email_blocks_with_a_sentence():
    result = score_completeness({**FULL, "email": None})
    assert result.blocked is True
    assert result.blocked_reason == "Can't send an intro — no email on file"
    assert "%" not in result.blocked_reason


def test_missing_company_blocks():
    result = score_completeness({**FULL, "company": None})
    assert result.blocked is True
    assert "company" in result.blocked_reason


def test_missing_both_gives_one_combined_sentence():
    result = score_completeness({**FULL, "email": None, "company": None})
    assert result.blocked is True
    assert result.blocked_reason.count("Can't") == 1


def test_non_blocking_gaps_do_not_block():
    result = score_completeness({**FULL, "linkedin_url": None, "bio": None})
    assert result.blocked is False
    assert result.blocked_reason is None
    assert result.score < 1.0


def test_gap_description_is_readable():
    result = score_completeness({**FULL, "linkedin_url": None, "bio": None})
    assert describe_gaps(result) == "Missing LinkedIn, bio"
    assert describe_gaps(score_completeness(FULL)) == "Complete"


def test_scoring_is_deterministic():
    record = {**FULL, "bio": None}
    assert score_completeness(record) == score_completeness(record)
