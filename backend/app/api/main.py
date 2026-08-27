"""FastAPI app: the operator's queue, and the actions that empty it.

One process serves both the JSON API and the built frontend, because the whole
thing is meant to run as a single container.

Two rules shape every response here:

* **Derived data is labelled as derived.** Enrichment comes back under its own
  key with its confidence and its evidence attached, never merged into the
  source record. The UI cannot accidentally render a model's guess as a fact
  because the API never hands it one that looks like a fact.
* **A record the backfill has not reached returns `enrichment: null`.** Not an
  empty object, not `unknown` strings -- null, so the UI can say "not yet
  enriched" and mean it. Partial coverage is the shipped state and it has to
  read as honest.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app import db
from backend.app.pipeline.completeness import describe_gaps, score_completeness

REPO_ROOT = Path(__file__).resolve().parents[3]
DIST = REPO_ROOT / "frontend" / "dist"

app = FastAPI(title="Offline — relationship intelligence", version="0.5.0")


# A pair the pipeline could not settle: it abstained, or survivorship found a
# field conflict it will not resolve on its own.
#
# Deliberately NOT "every merge". Rule 7 says a merge must be reversible by a
# person, not that a person must approve each one -- and a queue of forty-seven
# rubber-stamps is busywork that hides the three decisions that matter. Merges
# go to a log with an Undo; only genuine ambiguity goes to the queue.
# How many introductions Today offers at once. A day's work, not a backlog:
# an operator who is shown everything reads nothing.
DAILY_INTROS = 10

NEEDS_DECISION = """
    r.id IS NULL AND (
        d.verdict = 'insufficient_evidence'
        OR EXISTS (SELECT 1 FROM merge_groups g
                   WHERE g.status = 'pending_review'
                     AND g.reverted_at IS NULL
                     AND g.source_record_ids LIKE '%' || d.person_a_id || '%'
                     AND g.source_record_ids LIKE '%' || d.person_b_id || '%')
    )
"""


def conn() -> sqlite3.Connection:
    return db.connect()


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def person_summary(row: dict) -> dict:
    """The source record. Nothing derived is folded in."""
    return {
        "id": row["id"],
        "full_name": (row["full_name"] or "").strip() or None,
        "email": row["email"],
        "linkedin_url": row["linkedin_url"],
        "company": row["company"],
        "title": row["title"],
        "location": row["location"],
        "bio": row["bio"],
        "source": row["source"],
        "needs": loads(row["needs"], []),
        "offers": loads(row["offers"], []),
        "created_at": row["created_at"],
        "merged_into": row["merged_into"],
    }


def enrichment_payload(row: dict | None) -> dict | None:
    """None means 'the backfill has not reached this person yet'.

    Deliberately distinguishable from a person the model looked at and could not
    classify, who comes back with real fields set to `unknown` and a confidence.
    Those are different facts and the UI says different things about them.
    """
    if row is None or not row.get("confidence"):
        return None
    return {
        "persona": row["persona"],
        "seniority": row["seniority"],
        "company_stage": row["company_stage"],
        "sector": row["sector"],
        "geography": row["geography"],
        "needs": loads(row["needs"], []),
        "offers": loads(row["offers"], []),
        "confidence": row["confidence"],
        "low_confidence": bool(row["low_confidence"]),
        "evidence": loads(row["evidence"], []),
        "evidence_verified": row["evidence_verified"],
        "evidence_total": row["evidence_total"],
        "model": row["model"],
        "provider": row["provider"],
    }


def completeness_payload(person: dict) -> dict:
    result = score_completeness(person)
    return {
        "score": result.score,
        "missing": list(result.missing),
        "blocked": result.blocked,
        "blocked_reason": result.blocked_reason,
        "summary": describe_gaps(result),
    }


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


@app.get("/api/queue")
def queue() -> dict:
    c = conn()
    # The queue is "what has no human decision yet", not "what the pipeline
    # could not settle". An auto-merge is still a merge, and CLAUDE.md says
    # every merge needs a person -- so it appears here for confirmation until
    # somebody has actually looked at it.
    pending = rows(c.execute(f"""
        SELECT d.id, d.person_a_id, d.person_b_id, d.score, d.verdict,
               d.confidence, d.reason, d.stage, d.review_state
        FROM duplicate_pairs d
        LEFT JOIN duplicate_reviews r ON r.pair_id = d.id
        WHERE {NEEDS_DECISION}
        ORDER BY d.score DESC"""))

    # The headline number: what the pipeline settled on its own.
    auto_resolved = c.execute("""
        SELECT COUNT(*) FROM duplicate_pairs d
        LEFT JOIN duplicate_reviews r ON r.pair_id = d.id
        WHERE r.id IS NULL AND d.verdict != 'insufficient_evidence'""").fetchone()[0]
    merged_clusters = c.execute(
        "SELECT COUNT(*) FROM merge_groups WHERE status = 'merged'"
        " AND reverted_at IS NULL").fetchone()[0]

    incomplete = []
    for row in rows(c.execute(
            "SELECT * FROM people WHERE merged_into IS NULL ORDER BY id")):
        person = person_summary(row)
        gaps = completeness_payload(person)
        if gaps["blocked"] or gaps["score"] < 1.0:
            incomplete.append({**person, "completeness": gaps})
    incomplete.sort(key=lambda p: (not p["completeness"]["blocked"],
                                   p["completeness"]["score"]))

    applicants = rows(c.execute("""
        SELECT s.*, p.full_name, p.company, p.title
        FROM applicant_scores s JOIN people p ON p.id = s.person_id
        ORDER BY CASE s.band WHEN 'review' THEN 0 WHEN 'strong' THEN 1 ELSE 2 END,
                 s.total DESC"""))

    suggestions = c.execute(
        "SELECT COUNT(*) FROM introductions WHERE status = 'suggested'").fetchone()[0]
    with_copy = c.execute(
        "SELECT COUNT(*) FROM introductions WHERE status = 'suggested'"
        " AND why IS NOT NULL AND why != ''").fetchone()[0]

    # 265 cards is a backlog, not a queue -- the same mistake as putting every
    # auto-merge up for confirmation. Today shows the strongest DAILY_INTROS by
    # score; the rest stay one click away on the Introductions page, and the
    # total is stated so nothing is hidden.
    todays_batch = rows(c.execute("""
        SELECT i.*, a.full_name AS a_name, a.company AS a_company,
               b.full_name AS b_name, b.company AS b_company
        FROM introductions i
        JOIN people a ON a.id = i.person_a_id
        JOIN people b ON b.id = i.person_b_id
        WHERE i.status = 'suggested'
        ORDER BY i.score DESC LIMIT ?""", (DAILY_INTROS,)))

    enriched = c.execute(
        "SELECT COUNT(*) FROM enrichment WHERE confidence > 0").fetchone()[0]
    canonical = c.execute(
        "SELECT COUNT(*) FROM people WHERE merged_into IS NULL").fetchone()[0]
    c.close()

    return {
        "duplicates": {"count": len(pending), "items": pending[:8],
                       "auto_resolved": auto_resolved,
                       "merged_clusters": merged_clusters},
        "incomplete": {"count": len(incomplete),
                       "blocked": sum(1 for p in incomplete
                                      if p["completeness"]["blocked"]),
                       "items": incomplete[:8]},
        "applicants": {
            "count": len(applicants),
            "needs_review": sum(1 for a in applicants if a["band"] == "review"),
            "strong": sum(1 for a in applicants if a["band"] == "strong"),
            "items": applicants[:8],
        },
        "introductions": {"count": suggestions, "with_copy": with_copy,
                          "batch_size": DAILY_INTROS, "items": todays_batch},
        "coverage": {"enriched": enriched, "canonical": canonical,
                     "explained": c and 0},
    }


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


@app.get("/api/duplicates")
def duplicates(status: str = "pending") -> dict:
    c = conn()
    if status == "pending":
        where = NEEDS_DECISION
    elif status == "decided":
        where = "r.id IS NOT NULL"
    else:
        where = "1=1"

    pairs = rows(c.execute(f"""
        SELECT d.*, r.decision, r.decided_at
        FROM duplicate_pairs d
        LEFT JOIN duplicate_reviews r ON r.pair_id = d.id
        WHERE {where}
        ORDER BY d.score DESC, d.id"""))

    people = {r["id"]: person_summary(r)
              for r in rows(c.execute("SELECT * FROM people"))}
    enrichment = {r["person_id"]: r
                  for r in rows(c.execute("SELECT * FROM enrichment"))}
    held = {}
    for g in rows(c.execute("SELECT * FROM merge_groups WHERE status = 'pending_review'"
                            " AND reverted_at IS NULL")):
        for source in loads(g["source_record_ids"], []):
            held.setdefault(source, []).append(loads(g["conflicts"], []))

    out = []
    for pair in pairs:
        a, b = people.get(pair["person_a_id"]), people.get(pair["person_b_id"])
        if not (a and b):
            continue
        conflicts = held.get(pair["person_a_id"], [])
        out.append({
            **pair,
            "blocking_keys": loads(pair["blocking_keys"], []),
            # Why this pair is in the queue rather than merged: survivorship
            # found two values it will not silently pick between.
            "conflicts": conflicts[0] if conflicts else [],
            "a": {**a, "enrichment": enrichment_payload(enrichment.get(a["id"])),
                  "completeness": completeness_payload(a)},
            "b": {**b, "enrichment": enrichment_payload(enrichment.get(b["id"])),
                  "completeness": completeness_payload(b)},
        })
    remaining = c.execute(f"""
        SELECT COUNT(*) FROM duplicate_pairs d
        LEFT JOIN duplicate_reviews r ON r.pair_id = d.id
        WHERE {NEEDS_DECISION}""").fetchone()[0]
    resolved = c.execute("""
        SELECT COUNT(*) FROM duplicate_pairs
        WHERE verdict != 'insufficient_evidence'""").fetchone()[0]
    c.close()
    return {"pairs": out, "remaining": remaining, "auto_resolved": resolved}


class DuplicateDecision(BaseModel):
    decision: Literal["merge", "keep_both", "not_sure"]
    note: str | None = None


@app.post("/api/duplicates/{pair_id}/decision")
def decide_duplicate(pair_id: int, body: DuplicateDecision) -> dict:
    c = conn()
    pair = c.execute("SELECT * FROM duplicate_pairs WHERE id = ?", (pair_id,)).fetchone()
    if pair is None:
        c.close()
        raise HTTPException(404, "no such pair")

    now = db.utc_now()
    c.execute("""INSERT OR REPLACE INTO duplicate_reviews
                     (pair_id, decision, note, decided_at) VALUES (?, ?, ?, ?)""",
              (pair_id, body.decision, body.note, now))

    if body.decision == "merge":
        # The lower id survives; the other points at it. Reversible: the source
        # row is untouched and merged_into is a pointer, not a deletion.
        canonical = min(pair["person_a_id"], pair["person_b_id"])
        other = max(pair["person_a_id"], pair["person_b_id"])
        c.execute("UPDATE people SET merged_into = ? WHERE id = ?", (canonical, other))
        c.execute("UPDATE duplicate_pairs SET review_state = 'auto_merged' WHERE id = ?",
                  (pair_id,))
    else:
        c.execute("UPDATE people SET merged_into = NULL WHERE id IN (?, ?)",
                  (pair["person_a_id"], pair["person_b_id"]))
        state = "rejected" if body.decision == "keep_both" else "pending"
        c.execute("UPDATE duplicate_pairs SET review_state = ? WHERE id = ?",
                  (state, pair_id))
    c.commit()
    remaining = c.execute(f"""
        SELECT COUNT(*) FROM duplicate_pairs d
        LEFT JOIN duplicate_reviews r ON r.pair_id = d.id
        WHERE {NEEDS_DECISION}""").fetchone()[0]
    c.close()
    return {"ok": True, "pair_id": pair_id, "decision": body.decision,
            "remaining": remaining}


# ---------------------------------------------------------------------------
# Introductions
# ---------------------------------------------------------------------------


@app.get("/api/introductions")
def introductions(status: str = "suggested") -> dict:
    c = conn()
    where = "1=1" if status == "all" else "i.status = ?"
    params = () if status == "all" else (status,)
    items = rows(c.execute(
        f"SELECT * FROM introductions i WHERE {where} ORDER BY i.score DESC", params))

    people = {r["id"]: person_summary(r)
              for r in rows(c.execute("SELECT * FROM people"))}
    enrichment = {r["person_id"]: r
                  for r in rows(c.execute("SELECT * FROM enrichment"))}

    out = []
    for item in items:
        a, b = people.get(item["person_a_id"]), people.get(item["person_b_id"])
        if not (a and b):
            continue
        out.append({
            **item,
            "has_copy": bool(item["why"]),
            "a": {**a, "enrichment": enrichment_payload(enrichment.get(a["id"]))},
            "b": {**b, "enrichment": enrichment_payload(enrichment.get(b["id"]))},
        })
    counts = {r["status"]: r["n"] for r in rows(c.execute(
        "SELECT status, COUNT(*) AS n FROM introductions GROUP BY status"))}
    c.close()
    return {"introductions": out, "counts": counts}


class IntroDecision(BaseModel):
    decision: Literal["approve", "dismiss", "block"]


@app.post("/api/introductions/{intro_id}/decision")
def decide_intro(intro_id: int, body: IntroDecision) -> dict:
    c = conn()
    intro = c.execute("SELECT * FROM introductions WHERE id = ?", (intro_id,)).fetchone()
    if intro is None:
        c.close()
        raise HTTPException(404, "no such introduction")

    now = db.utc_now()
    status = {"approve": "approved", "dismiss": "dismissed",
              "block": "dismissed"}[body.decision]
    c.execute("UPDATE introductions SET status = ?, decided_at = ? WHERE id = ?",
              (status, now, intro_id))

    if body.decision == "block":
        # "Never suggest this pair" outlives the suggestion: the engine reads
        # blocked_pairs before it scores anything.
        a = min(intro["person_a_id"], intro["person_b_id"])
        b = max(intro["person_a_id"], intro["person_b_id"])
        c.execute("""INSERT OR REPLACE INTO blocked_pairs
                         (person_a_id, person_b_id, reason, created_at)
                     VALUES (?, ?, ?, ?)""",
                  (a, b, "operator: never suggest this pair", now))
    c.commit()
    c.close()
    return {"ok": True, "id": intro_id, "status": status}


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@app.get("/api/people")
def people(q: str = "", persona: str = "", band: str = "",
           incomplete: bool = False, limit: int = 500) -> dict:
    c = conn()
    records = rows(c.execute(
        "SELECT * FROM people WHERE merged_into IS NULL ORDER BY id"))
    enrichment = {r["person_id"]: r
                  for r in rows(c.execute("SELECT * FROM enrichment"))}
    scores = {r["person_id"]: r
              for r in rows(c.execute("SELECT * FROM applicant_scores"))}
    c.close()

    needle = q.strip().lower()
    out = []
    for row in records:
        person = person_summary(row)
        enr = enrichment_payload(enrichment.get(person["id"]))
        gaps = completeness_payload(person)
        score = scores.get(person["id"])

        if needle:
            haystack = " ".join(str(person.get(f) or "") for f in
                                ("full_name", "company", "title", "email", "location"))
            if needle not in haystack.lower():
                continue
        if persona and (enr or {}).get("persona") != persona:
            continue
        if band and (score or {}).get("band") != band:
            continue
        if incomplete and not (gaps["blocked"] or gaps["score"] < 1.0):
            continue

        out.append({
            **person,
            "enrichment": enr,
            "completeness": gaps,
            "applicant": ({"total": score["total"], "band": score["band"]}
                          if score else None),
        })
    return {"people": out[:limit], "total": len(out)}


@app.get("/api/people/{person_id}")
def person_detail(person_id: str) -> dict:
    c = conn()
    row = c.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if row is None:
        c.close()
        raise HTTPException(404, "no such person")
    person = person_summary(dict(row))

    enr = c.execute("SELECT * FROM enrichment WHERE person_id = ?",
                    (person_id,)).fetchone()
    score = c.execute("SELECT * FROM applicant_scores WHERE person_id = ?",
                      (person_id,)).fetchone()
    application = c.execute("SELECT * FROM applications WHERE person_id = ?",
                            (person_id,)).fetchone()

    suggestions = rows(c.execute(
        "SELECT * FROM introductions WHERE person_a_id = ? OR person_b_id = ?"
        " ORDER BY score DESC", (person_id, person_id)))
    others = {}
    for s in suggestions:
        other_id = s["person_b_id"] if s["person_a_id"] == person_id else s["person_a_id"]
        other = c.execute("SELECT * FROM people WHERE id = ?", (other_id,)).fetchone()
        if other:
            others[s["id"]] = person_summary(dict(other))

    merged = rows(c.execute("SELECT * FROM people WHERE merged_into = ?", (person_id,)))
    c.close()

    applicant = None
    if score:
        applicant = {
            **dict(score),
            "signals": loads(score["signals"], []),
            "bullets": loads(score["bullets"], []),
            "unsupported_numbers": loads(score["unsupported_numbers"], []),
            "application": dict(application) if application else None,
        }

    return {
        "person": person,
        "enrichment": enrichment_payload(dict(enr) if enr else None),
        "completeness": completeness_payload(person),
        "applicant": applicant,
        "suggestions": [{**s, "other": others.get(s["id"])} for s in suggestions],
        "merged_records": [person_summary(m) for m in merged],
    }


# ---------------------------------------------------------------------------
# Merges: a reversible log, not a queue
# ---------------------------------------------------------------------------


@app.get("/api/merges")
def merges(limit: int = 100) -> dict:
    """What the pipeline merged on its own, newest first, each one undoable."""
    c = conn()
    groups = rows(c.execute("""
        SELECT * FROM merge_groups
        WHERE status = 'merged' AND reverted_at IS NULL
        ORDER BY id DESC LIMIT ?""", (limit,)))
    people = {r["id"]: person_summary(r)
              for r in rows(c.execute("SELECT * FROM people"))}

    out = []
    for g in groups:
        source_ids = loads(g["source_record_ids"], [])
        out.append({
            "id": g["id"],
            "canonical_person_id": g["canonical_person_id"],
            "decided_by": g["decided_by"],
            "created_at": g["created_at"],
            "source_record_ids": source_ids,
            "records": [people[i] for i in source_ids if i in people],
            "resolved": loads(g["resolved"], {}),
            "provenance": loads(g["provenance"], {}),
        })
    reverted = c.execute(
        "SELECT COUNT(*) FROM merge_groups WHERE reverted_at IS NOT NULL").fetchone()[0]
    c.close()
    return {"merges": out, "reverted": reverted}


@app.post("/api/merges/{group_id}/undo")
def undo_merge(group_id: int) -> dict:
    """Undo a merge. Nothing was deleted, so nothing has to be reconstructed."""
    from backend.app.pipeline.store import revert_merge

    c = conn()
    group = c.execute("SELECT * FROM merge_groups WHERE id = ?", (group_id,)).fetchone()
    if group is None or group["reverted_at"]:
        c.close()
        raise HTTPException(404, "no active merge with that id")
    result = revert_merge(c, group["canonical_person_id"],
                          note="undone from the merge log")
    c.close()
    return {"ok": True, **result}


@app.get("/api/applicants")
def applicants() -> dict:
    c = conn()
    items = rows(c.execute("""
        SELECT s.*, p.full_name, p.company, p.title, p.location,
               e.confidence AS enrichment_confidence
        FROM applicant_scores s
        JOIN people p ON p.id = s.person_id
        LEFT JOIN enrichment e ON e.person_id = s.person_id
        ORDER BY s.total DESC"""))
    c.close()
    return {"applicants": [
        {**item,
         "signals": loads(item["signals"], []),
         "bullets": loads(item["bullets"], []),
         # The UI needs to say "this score is limited by a thin record" rather
         # than presenting a low number as a judgment about the person.
         "enrichment_missing": not item["enrichment_confidence"]}
        for item in items]}


@app.get("/api/health")
def health() -> dict:
    c = conn()
    counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("people", "enrichment", "applicant_scores", "introductions")}
    enriched = c.execute(
        "SELECT COUNT(*) FROM enrichment WHERE confidence > 0").fetchone()[0]
    canonical = c.execute(
        "SELECT COUNT(*) FROM people WHERE merged_into IS NULL").fetchone()[0]
    explained = c.execute(
        "SELECT COUNT(*) FROM applicant_scores WHERE explanation != ''").fetchone()[0]
    with_copy = c.execute(
        "SELECT COUNT(*) FROM introductions WHERE why != ''").fetchone()[0]
    c.close()
    return {"ok": True, "counts": counts,
            "coverage": {"canonical": canonical, "enriched": enriched,
                         "explained": explained, "intros_with_copy": with_copy}}


# ---------------------------------------------------------------------------
# Static frontend, mounted last so it never shadows /api
# ---------------------------------------------------------------------------

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
