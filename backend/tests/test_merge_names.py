"""Name and company survivorship: the fullest form wins, not the newest.

Split out from test_merge.py because these two rules are the ones that decide
whether a merge is lossless. Everything else in survivorship picks between two
equally valid values; these two pick between a value and a degraded copy of it.
"""

from __future__ import annotations

from backend.app.pipeline import merge
from backend.app.pipeline.records import normalize_record

BASE = {
    "id": "p-0001",
    "full_name": "Ines Bakshi",
    "email": "ines.bakshi@vantagesystems.com",
    "linkedin_url": "https://www.linkedin.com/in/ines-bakshi-2291",
    "company": "Vantage Systems",
    "title": "Solutions Architect",
    "location": "Bengaluru, India",
    "bio": "Solutions Architect at Vantage Systems.",
    "source": "airtable_export",
    "needs": [],
    "offers": [],
    "created_at": "2024-01-01",
}


def rec(**overrides):
    return normalize_record({**BASE, **overrides})


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------

def test_ines_b_does_not_overwrite_ines_bakshi():
    """The case that motivated the rule: an abbreviated later re-entry must not
    win just because it arrived second."""
    full = rec(id="p-0010", full_name="Ines Bakshi", created_at="2023-02-01")
    abbreviated = rec(id="p-0011", full_name="Ines B.", created_at="2025-11-01")

    plan = merge.plan_merge([full, abbreviated])
    assert plan.resolved["full_name"] == "Ines Bakshi"
    assert plan.provenance["full_name"] == "p-0010"


def test_the_rule_is_not_just_recency_reversed():
    """A fuller name that happens to be newer still wins."""
    abbreviated = rec(id="p-0010", full_name="I. Bakshi", created_at="2023-02-01")
    full = rec(id="p-0011", full_name="Ines Bakshi", created_at="2025-11-01")
    assert merge.plan_merge([abbreviated, full]).resolved["full_name"] == "Ines Bakshi"


def test_initial_surname_loses_to_full_surname():
    full = rec(id="p-0010", full_name="Priya Raghavan")
    abbreviated = rec(id="p-0011", full_name="P. Raghavan", created_at="2025-11-01")
    assert merge.plan_merge([full, abbreviated]).resolved["full_name"] == "Priya Raghavan"


def test_proper_casing_breaks_a_tie_between_equal_names():
    shouted = rec(id="p-0010", full_name="INES BAKSHI", created_at="2025-11-01")
    cased = rec(id="p-0011", full_name="Ines Bakshi", created_at="2023-01-01")
    assert merge.plan_merge([shouted, cased]).resolved["full_name"] == "Ines Bakshi"


def test_surrounding_whitespace_is_trimmed():
    padded = rec(id="p-0010", full_name="  Ines Bakshi  ", created_at="2025-11-01")
    other = rec(id="p-0011", full_name="Ines B.", created_at="2023-01-01")
    assert merge.plan_merge([padded, other]).resolved["full_name"] == "Ines Bakshi"


def test_a_three_way_cluster_keeps_the_fullest_of_all_three():
    a = rec(id="p-0010", full_name="Ines B.", created_at="2025-11-01")
    b = rec(id="p-0011", full_name="INES BAKSHI", created_at="2024-01-01")
    c = rec(id="p-0012", full_name="Ines Bakshi", created_at="2023-01-01")
    assert merge.plan_merge([a, b, c]).resolved["full_name"] == "Ines Bakshi"


def test_name_choice_is_order_independent():
    a = rec(id="p-0010", full_name="Ines B.", created_at="2025-11-01")
    b = rec(id="p-0011", full_name="Ines Bakshi", created_at="2023-01-01")
    assert merge.plan_merge([a, b]).resolved["full_name"] == \
           merge.plan_merge([b, a]).resolved["full_name"]


def test_a_cluster_with_one_usable_name_still_resolves():
    named = rec(id="p-0010", full_name="Ines Bakshi")
    nameless = rec(id="p-0011", full_name=None, created_at="2025-11-01")
    assert merge.plan_merge([named, nameless]).resolved["full_name"] == "Ines Bakshi"


# ---------------------------------------------------------------------------
# Company
# ---------------------------------------------------------------------------

def test_abbreviated_company_loses_to_the_full_name():
    short = rec(id="p-0010", company="Delta", created_at="2025-11-01")
    full = rec(id="p-0011", company="Delta AI", created_at="2023-01-01")
    plan = merge.plan_merge([short, full])
    assert plan.resolved["company"] == "Delta AI"
    assert plan.provenance["company"] == "p-0011"


def test_an_abbreviation_is_not_a_conflict():
    """`Delta` and `Delta AI` are one employer written two ways. Sending that
    to a human wastes the queue on a formatting difference."""
    short = rec(id="p-0010", company="Delta")
    full = rec(id="p-0011", company="Delta AI")
    plan = merge.plan_merge([short, full])
    assert plan.conflicts == []
    assert plan.requires_review is False


def test_genuinely_different_employers_are_still_a_conflict():
    a = rec(id="p-0010", company="Delta AI")
    b = rec(id="p-0011", company="Helix Grid")
    plan = merge.plan_merge([a, b])
    assert [c.field for c in plan.conflicts] == ["company"]
    assert plan.requires_review


def test_a_short_fragment_does_not_swallow_an_employer():
    """Two characters is not an abbreviation, it is noise. `De` must not be
    treated as shorthand for `Delta AI`."""
    assert merge._is_abbreviation_of("de", "deltaai") is False
    assert merge._is_abbreviation_of("delta", "deltaai") is True
    assert merge._is_abbreviation_of("delta", "helixgrid") is False


def test_legal_suffix_variants_still_pick_a_readable_form():
    a = rec(id="p-0010", company="Vantage Systems", created_at="2023-01-01")
    b = rec(id="p-0011", company="VANTAGE SYSTEMS Pvt Ltd", created_at="2025-01-01")
    plan = merge.plan_merge([a, b])
    assert plan.conflicts == []
    assert plan.resolved["company"] in {"Vantage Systems", "VANTAGE SYSTEMS Pvt Ltd"}


def test_company_choice_is_order_independent():
    a = rec(id="p-0010", company="Delta", created_at="2025-11-01")
    b = rec(id="p-0011", company="Delta AI", created_at="2023-01-01")
    assert merge.plan_merge([a, b]).resolved["company"] == \
           merge.plan_merge([b, a]).resolved["company"]


def test_merging_never_loses_a_field_it_had():
    """The whole point: no resolved field may be emptier than the best source."""
    a = rec(id="p-0010", full_name="Ines Bakshi", company="Delta AI", bio="A long biography here.")
    b = rec(id="p-0011", full_name="Ines B.", company="Delta", bio="Short.",
            created_at="2025-11-01")
    plan = merge.plan_merge([a, b])
    assert len(plan.resolved["full_name"]) >= len("Ines B.")
    assert len(plan.resolved["company"]) >= len("Delta")
    assert len(plan.resolved["bio"]) >= len("Short.")
