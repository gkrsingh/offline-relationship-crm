"""Reverting a merge, and making the reversal stick.

A merge that can be undone once but comes back on the next pipeline run is not
reversible in any useful sense. These tests cover both halves: the undo itself,
and the fact that recomputation respects what a person decided.
"""

from __future__ import annotations

import json

import pytest

from backend.app import db
from backend.app.pipeline import dedupe, merge, store
from backend.app.pipeline.records import normalize_record

BASE = {
    "id": "p-0001",
    "full_name": "Rohit Zaidi",
    "email": "rohit@willowcollective.com",
    "linkedin_url": "https://www.linkedin.com/in/rohit-zaidi-1",
    "company": "Willow Collective",
    "title": "GM, India",
    "location": "Pune, India",
    "bio": "Operator at Willow Collective.",
    "source": "airtable_export",
    "needs": [],
    "offers": [],
    "created_at": "2024-01-01",
}


def raw(**overrides):
    return {**BASE, **overrides}


@pytest.fixture()
def merged_db(tmp_path):
    """A database with one auto-merged cluster of two records."""
    conn = db.connect(tmp_path / "revert.db")
    db.apply_schema(conn)

    records = [raw(id="p-0001"), raw(id="p-0002", created_at="2025-06-01")]
    db.load_people(conn, records)

    normalized = [normalize_record(r) for r in records]
    result = dedupe.run(normalized, provider=None)
    plan = merge.plan_merge(normalized, decided_by="stage2_fuzzy")

    store.write_pairs(conn, result.pairs)
    store.write_merges(conn, [plan])
    yield conn, plan
    conn.close()


def test_the_fixture_really_did_merge(merged_db):
    conn, plan = merged_db
    merged = conn.execute(
        "SELECT COUNT(*) FROM people WHERE merged_into IS NOT NULL").fetchone()[0]
    assert merged == 1
    assert conn.execute(
        "SELECT status FROM merge_groups").fetchone()["status"] == "merged"


def test_revert_restores_both_records(merged_db):
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id)

    rows = conn.execute("SELECT id, merged_into FROM people ORDER BY id").fetchall()
    assert [r["merged_into"] for r in rows] == [None, None]


def test_revert_deletes_nothing(merged_db):
    """The source rows are the whole point: a reverted merge must leave the
    originals byte-identical, not reconstruct them."""
    conn, plan = merged_db
    before = conn.execute(
        "SELECT id, full_name, email, company, title FROM people ORDER BY id").fetchall()

    store.revert_merge(conn, plan.canonical_id)

    after = conn.execute(
        "SELECT id, full_name, email, company, title FROM people ORDER BY id").fetchall()
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_revert_keeps_the_merge_group_as_history(merged_db):
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id, note="different people")

    group = conn.execute("SELECT * FROM merge_groups").fetchone()
    assert group["status"] == "reverted"
    assert group["reverted_at"]
    assert json.loads(group["source_record_ids"]) == list(plan.source_ids)
    assert group["resolved"], "the survivorship result is kept for inspection"


def test_revert_records_a_human_decision(merged_db):
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id, note="colleagues, not one person")

    review = conn.execute("SELECT * FROM duplicate_reviews").fetchone()
    assert review["decision"] == "keep_both"
    assert review["note"] == "colleagues, not one person"
    assert store.human_decisions(conn) == {("p-0001", "p-0002"): "keep_both"}


def test_reverting_twice_is_refused(merged_db):
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id)
    with pytest.raises(ValueError):
        store.revert_merge(conn, plan.canonical_id)


def test_a_human_decision_survives_recomputation(merged_db):
    """Rewriting duplicate_pairs must not cascade away the review attached to it."""
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id)

    normalized = store.load_records(conn)
    rerun = dedupe.run(normalized, provider=None)
    store.write_pairs(conn, rerun.pairs)

    assert store.human_decisions(conn) == {("p-0001", "p-0002"): "keep_both"}
    pair = conn.execute("SELECT review_state FROM duplicate_pairs").fetchone()
    assert pair["review_state"] == "rejected"


def test_recomputation_does_not_resurrect_a_reverted_merge(merged_db):
    """The end-to-end property: revert, run the whole pipeline again, and the
    two records are still two records."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from backend.scripts.run_pipeline import build_merge_plans

    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id)

    normalized = store.load_records(conn)
    decisions = store.human_decisions(conn)
    rerun = dedupe.run(normalized, provider=None)
    plans = build_merge_plans(normalized, rerun.pairs, decisions)

    assert plans == [], "a keep_both decision must remove the merge edge"

    store.write_merges(conn, plans)
    rows = conn.execute("SELECT merged_into FROM people").fetchall()
    assert [r["merged_into"] for r in rows] == [None, None]


def test_reverted_history_survives_a_later_run(merged_db):
    conn, plan = merged_db
    store.revert_merge(conn, plan.canonical_id)
    store.write_merges(conn, [])   # a later run that merges nothing

    kept = conn.execute(
        "SELECT COUNT(*) FROM merge_groups WHERE reverted_at IS NOT NULL").fetchone()[0]
    assert kept == 1
