"""Schema and loader tests, plus the guard that keeps ground truth out of the app."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app import db
from backend.scripts import generate_data as gen

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TABLES = {
    "applicant_scores", "applications", "blocked_pairs", "duplicate_pairs",
    "duplicate_reviews", "embeddings", "enrichment", "introductions",
    "merge_groups", "people", "people_normalized", "pipeline_runs",
}


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.apply_schema(connection)
    yield connection
    connection.close()


def test_schema_creates_every_table(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    assert {r[0] for r in rows} == EXPECTED_TABLES


def test_schema_is_idempotent(conn):
    db.apply_schema(conn)  # second application must not raise
    rows = conn.execute("SELECT COUNT(*) FROM people")
    assert rows.fetchone()[0] == 0


def test_load_people_round_trips_records(conn):
    records, applications, _gt = gen.generate(seed=42, canonical_count=40)
    assert db.load_people(conn, records) == len(records)
    assert db.load_applications(conn, applications) == len(applications)

    row = conn.execute(
        "SELECT * FROM people WHERE id = ?", (records[0]["id"],)).fetchone()
    assert row["full_name"] == records[0]["full_name"]
    assert json.loads(row["needs"]) == records[0]["needs"]
    assert row["merged_into"] is None
    assert row["ingested_at"]


def test_load_people_is_idempotent(conn):
    records, _apps, _gt = gen.generate(seed=42, canonical_count=40)
    db.load_people(conn, records)
    db.load_people(conn, records)
    assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == len(records)


def test_application_requires_an_existing_person(conn):
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO applications (person_id, why_join) VALUES ('p-9999', 'x')")
        conn.commit()
