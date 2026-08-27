"""Survivorship rules and clustering."""

from __future__ import annotations

import pytest

from backend.app.pipeline import merge
from backend.app.pipeline.records import normalize_record

BASE = {
    "id": "p-0001",
    "full_name": "Priya Raghavan",
    "email": "priya.raghavan@vantagesystems.com",
    "linkedin_url": "https://www.linkedin.com/in/priya-raghavan-2291",
    "company": "Vantage Systems",
    "title": "VP Sales",
    "location": "Bengaluru, India",
    "bio": "Short bio.",
    "source": "airtable_export",
    "needs": ["hiring senior GTM talent"],
    "offers": ["B2B GTM expertise"],
    "created_at": "2024-01-01",
}


def rec(**overrides):
    return normalize_record({**BASE, **overrides})


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def test_transitive_edges_form_one_cluster():
    assert merge.cluster_ids([("a", "b"), ("b", "c")]) == [["a", "b", "c"]]


def test_unrelated_edges_stay_separate():
    assert merge.cluster_ids([("a", "b"), ("c", "d")]) == [["a", "b"], ["c", "d"]]


def test_clustering_is_order_independent():
    assert merge.cluster_ids([("b", "c"), ("a", "b")]) == \
           merge.cluster_ids([("a", "b"), ("b", "c")])


# ---------------------------------------------------------------------------
# Survivorship rules
# ---------------------------------------------------------------------------

def test_rule1_most_recent_non_null_wins():
    old = rec(id="p-1", created_at="2023-01-01", title="VP Sales")
    new = rec(id="p-2", created_at="2025-06-01", title="Chief of Staff")
    plan = merge.plan_merge([old, new])
    assert plan.resolved["title"] == "Chief of Staff"
    assert plan.provenance["title"] == "p-2"


def test_rule1_skips_nulls_rather_than_blanking_a_field():
    old = rec(id="p-1", created_at="2023-01-01", title="VP Sales")
    new = rec(id="p-2", created_at="2025-06-01", title=None)
    plan = merge.plan_merge([old, new])
    assert plan.resolved["title"] == "VP Sales"


def test_rule2_work_email_beats_a_newer_personal_one():
    work = rec(id="p-1", created_at="2023-01-01",
               email="priya.raghavan@vantagesystems.com")
    personal = rec(id="p-2", created_at="2025-06-01", email="priya.r@gmail.com")
    plan = merge.plan_merge([work, personal])
    assert plan.resolved["email"] == "priya.raghavan@vantagesystems.com"
    assert plan.conflicts == []


def test_rule3_longest_bio_beats_the_newest():
    short = rec(id="p-1", created_at="2025-06-01", bio="VP Sales.")
    long = rec(id="p-2", created_at="2023-01-01",
               bio="VP Sales at Vantage Systems, previously eight years in enterprise software.")
    plan = merge.plan_merge([short, long])
    assert plan.resolved["bio"].startswith("VP Sales at Vantage")
    assert plan.provenance["bio"] == "p-2"


def test_rule4_needs_and_offers_are_unioned():
    a = rec(id="p-1", needs=["hiring senior GTM talent"], offers=["B2B GTM expertise"])
    b = rec(id="p-2", needs=["finding a CFO"], offers=["B2B GTM expertise", "fundraising"])
    plan = merge.plan_merge([a, b])
    assert set(plan.resolved["needs"]) == {"hiring senior GTM talent", "finding a CFO"}
    assert set(plan.resolved["offers"]) == {"B2B GTM expertise", "fundraising"}


def test_rule5_earliest_created_at_is_first_contact():
    a = rec(id="p-1", created_at="2023-04-05")
    b = rec(id="p-2", created_at="2025-06-01")
    plan = merge.plan_merge([a, b])
    assert plan.first_contact_at == "2023-04-05"
    assert plan.resolved["created_at"] == "2023-04-05"


def test_rule6_every_source_id_is_preserved():
    plan = merge.plan_merge([rec(id="p-1"), rec(id="p-2"), rec(id="p-3")])
    assert plan.resolved["source_record_ids"] == ["p-1", "p-2", "p-3"]
    assert plan.canonical_id in plan.source_ids


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

def test_two_work_emails_is_a_conflict():
    a = rec(id="p-1", email="priya@vantagesystems.com")
    b = rec(id="p-2", email="p.raghavan@vantagesystems.com")
    plan = merge.plan_merge([a, b])
    assert plan.requires_review
    assert [c.field for c in plan.conflicts] == ["email"]


def test_two_personal_emails_is_also_a_conflict():
    a = rec(id="p-1", email="priya.r@gmail.com")
    b = rec(id="p-2", email="raghavan.priya@gmail.com")
    plan = merge.plan_merge([a, b])
    assert plan.requires_review


def test_differing_companies_is_a_conflict():
    a = rec(id="p-1", company="Vantage Systems")
    b = rec(id="p-2", company="Obsidian Health")
    plan = merge.plan_merge([a, b])
    assert "company" in [c.field for c in plan.conflicts]


def test_company_suffix_variants_are_not_a_conflict():
    a = rec(id="p-1", company="Vantage Systems")
    b = rec(id="p-2", company="VANTAGE SYSTEMS Pvt Ltd")
    plan = merge.plan_merge([a, b])
    assert plan.conflicts == []
    assert not plan.requires_review


def test_a_clean_cluster_needs_no_review():
    a = rec(id="p-1", created_at="2023-01-01")
    b = rec(id="p-2", created_at="2024-01-01", title="Vice President of Sales")
    assert merge.plan_merge([a, b]).requires_review is False


# ---------------------------------------------------------------------------
# Determinism and guards
# ---------------------------------------------------------------------------

def test_merge_is_order_independent():
    a = rec(id="p-1", created_at="2023-01-01", bio="Short.")
    b = rec(id="p-2", created_at="2025-01-01", bio="A considerably longer biography.")
    forward = merge.plan_merge([a, b])
    backward = merge.plan_merge([b, a])
    assert forward.resolved == backward.resolved
    assert forward.canonical_id == backward.canonical_id


def test_canonical_record_is_the_most_complete_one():
    full = rec(id="p-2")
    sparse = rec(id="p-1", email=None, company=None, bio=None, linkedin_url=None)
    assert merge.plan_merge([sparse, full]).canonical_id == "p-2"


def test_a_merge_needs_two_records():
    with pytest.raises(ValueError):
        merge.plan_merge([rec(id="p-1")])
