"""Every introduction decision is reversible, and none can be repeated.

The rule the UI enforces -- an operator is always offered the way back out of a
decision and never offered the same decision twice -- only holds if the API can
actually take a card back. These tests cover the round trip for all three
decisions, and the two things a naive `restore` would get wrong: leaving a
never-suggest in place so the pair is filtered straight back out, and leaving a
decision timestamp on a card nobody has decided.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import db

PEOPLE = [
    {"id": "p-0001", "full_name": "Rohit Zaidi", "email": "rohit@willow.com",
     "linkedin_url": "https://www.linkedin.com/in/rohit-zaidi-1",
     "company": "Willow Collective", "title": "GM, India", "location": "Pune, India",
     "bio": "Operator.", "source": "airtable_export", "needs": [], "offers": [],
     "created_at": "2024-01-01"},
    {"id": "p-0002", "full_name": "Uma Kumar", "email": "uma@ridgeloop.com",
     "linkedin_url": "https://www.linkedin.com/in/uma-kumar-2",
     "company": "Ridge Loop", "title": "COO", "location": "Bengaluru, India",
     "bio": "Operator.", "source": "airtable_export", "needs": [], "offers": [],
     "created_at": "2024-02-01"},
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    conn = db.connect(path)
    db.apply_schema(conn)
    db.load_people(conn, PEOPLE)
    conn.execute(
        """INSERT INTO introductions
               (id, person_a_id, person_b_id, score, why, draft_message,
                status, created_at)
           VALUES (1, 'p-0001', 'p-0002', 0.9, 'They both work in ops.',
                   'Hello.', 'suggested', ?)""",
        (db.utc_now(),))
    conn.commit()
    conn.close()

    from backend.app.api import main
    # conn() binds the default path at import time, so the seam is the function.
    monkeypatch.setattr(main, "conn", lambda: db.connect(path))
    client = TestClient(main.app)
    client.db_path = path
    return client


def state(client) -> tuple[str, dict]:
    body = client.get("/api/introductions?status=all").json()
    return body["introductions"][0]["status"], body["counts"]


def decide(client, decision: str) -> str:
    r = client.post("/api/introductions/1/decision", json={"decision": decision})
    assert r.status_code == 200, r.text
    return r.json()["status"]


def test_every_tab_has_a_count_even_when_it_holds_nothing(client):
    _, counts = state(client)
    assert counts == {"suggested": 1, "approved": 0, "dismissed": 0}
    # The tabs add up to the total, which is what lets the nav show one number.
    assert sum(counts.values()) == 1


@pytest.mark.parametrize("decision, landed", [
    ("approve", "approved"),
    ("dismiss", "dismissed"),
    ("block", "dismissed"),
])
def test_a_decision_can_always_be_reversed(client, decision, landed):
    assert decide(client, decision) == landed
    assert state(client)[0] == landed

    assert decide(client, "restore") == "suggested"
    status, counts = state(client)
    assert status == "suggested"
    assert counts == {"suggested": 1, "approved": 0, "dismissed": 0}


def test_restoring_a_blocked_pair_lifts_the_block(client):
    decide(client, "block")
    assert blocked(client) == 1

    decide(client, "restore")
    # Without this, the card returns to the queue and the next engine run
    # filters it straight back out -- a restore that silently does nothing.
    assert blocked(client) == 0


def test_a_restored_card_carries_no_decision_time(client):
    decide(client, "approve")
    assert decided_at(client) is not None

    decide(client, "restore")
    assert decided_at(client) is None


def test_an_unknown_decision_is_refused(client):
    r = client.post("/api/introductions/1/decision", json={"decision": "delete"})
    assert r.status_code == 422


# --- helpers reading the database the API just wrote to -------------------

def _conn(client):
    return db.connect(client.db_path)


def blocked(client) -> int:
    conn = _conn(client)
    try:
        return conn.execute("SELECT COUNT(*) FROM blocked_pairs").fetchone()[0]
    finally:
        conn.close()


def decided_at(client):
    conn = _conn(client)
    try:
        return conn.execute(
            "SELECT decided_at FROM introductions WHERE id = 1").fetchone()[0]
    finally:
        conn.close()
