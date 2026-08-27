"""Database I/O for the pipeline.

Kept separate from normalize/dedupe/merge so those stay pure and unit-testable
without a database. This module is the only place in the pipeline that knows
SQL exists.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Sequence

from backend.app.db import utc_now
from backend.app.pipeline.dedupe import Pair
from backend.app.pipeline.enrich import EnrichedPerson
from backend.app.pipeline.merge import MergePlan
from backend.app.pipeline.records import NormalizedRecord, normalize_record


def load_records(conn: sqlite3.Connection) -> list[NormalizedRecord]:
    """Read raw people and normalize them in memory. Raw rows are not touched."""
    rows = conn.execute("SELECT * FROM people ORDER BY id").fetchall()
    return [normalize_record(row) for row in rows]


def write_normalized(conn: sqlite3.Connection, records: Sequence[NormalizedRecord]) -> int:
    now = utc_now()
    conn.execute("DELETE FROM people_normalized")
    conn.executemany(
        """
        INSERT INTO people_normalized (
            person_id, name_normalized, name_first, name_last,
            email_normalized, email_local, email_domain, email_is_personal,
            linkedin_handle, company_normalized, title_normalized, title_canonical,
            location_city, location_country, needs_text, offers_text, name_tokens,
            completeness, missing_fields, is_blocked, blocked_reason, normalized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.id, r.name_normalized or None, r.first_name, r.last_name,
                r.email_normalized, r.email_local,
                r.email.domain if r.email else None,
                int(r.is_personal_email) if r.email else None,
                r.linkedin_slug, r.company_canonical, r.title_clean, r.title_canonical,
                r.city, r.country,
                " ".join(r.needs) or None, " ".join(r.offers) or None,
                json.dumps(list(r.name.tokens) if r.name else []),
                r.completeness.score, json.dumps(list(r.completeness.missing)),
                int(r.completeness.blocked), r.completeness.blocked_reason, now,
            )
            for r in records
        ],
    )
    conn.commit()
    return len(records)


def write_pairs(conn: sqlite3.Connection, pairs: Sequence[Pair]) -> int:
    """Rewrite the machine verdicts, carrying human decisions across.

    duplicate_reviews cascades off duplicate_pairs, so a naive rewrite would
    silently delete every decision an operator has made. Recomputation is
    allowed to change what the pipeline thinks; it is not allowed to forget
    what a person decided.
    """
    now = utc_now()
    kept_reviews = conn.execute(
        """SELECT p.person_a_id AS a, p.person_b_id AS b,
                  r.decision, r.note, r.decided_at
           FROM duplicate_reviews r JOIN duplicate_pairs p ON p.id = r.pair_id"""
    ).fetchall()

    conn.execute("DELETE FROM duplicate_pairs")
    conn.executemany(
        """
        INSERT INTO duplicate_pairs (
            person_a_id, person_b_id, stage, method, score, verdict, confidence,
            reason, llm_used, review_state, blocking_keys, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            # a < b is enforced here so a pair can never exist under two orderings.
            (min(p.a_id, p.b_id), max(p.a_id, p.b_id), p.stage, p.method, p.score,
             p.verdict, p.confidence, p.reason, int(p.llm_used), p.review_state,
             json.dumps(list(p.blocking_keys)), now)
            for p in pairs
        ],
    )

    for review in kept_reviews:
        pair = conn.execute(
            "SELECT id FROM duplicate_pairs WHERE person_a_id = ? AND person_b_id = ?",
            (review["a"], review["b"])).fetchone()
        if pair is None:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO duplicate_reviews (pair_id, decision, note, decided_at)
               VALUES (?, ?, ?, ?)""",
            (pair["id"], review["decision"], review["note"], review["decided_at"]))
        if review["decision"] == "keep_both":
            conn.execute("UPDATE duplicate_pairs SET review_state = 'rejected' WHERE id = ?",
                         (pair["id"],))

    conn.commit()
    return len(pairs)


def write_merges(conn: sqlite3.Connection, plans: Sequence[MergePlan]) -> tuple[int, int]:
    """Persist merge groups and point merged rows at their canonical record.

    Nothing is deleted. A merge is undone by clearing `merged_into` and setting
    `reverted_at`, which is why the source ids are stored on the group.
    """
    now = utc_now()
    # Reverted groups are history, not state to recompute. Keeping them is the
    # audit trail that makes a merge genuinely reversible rather than merely
    # undoable once.
    conn.execute("DELETE FROM merge_groups WHERE reverted_at IS NULL")
    conn.execute("UPDATE people SET merged_into = NULL")

    merged = pending = 0
    for plan in plans:
        status = "pending_review" if plan.requires_review else "merged"
        merged += status == "merged"
        pending += status == "pending_review"

        conn.execute(
            """
            INSERT INTO merge_groups (
                canonical_person_id, source_record_ids, decided_by, status,
                resolved, provenance, conflicts, first_contact_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.canonical_id, json.dumps(list(plan.source_ids)),
                plan.decided_by, status,
                json.dumps(plan.resolved, ensure_ascii=False),
                json.dumps(plan.provenance),
                json.dumps([c.__dict__ for c in plan.conflicts], ensure_ascii=False),
                plan.first_contact_at, now,
            ),
        )
        # A cluster with a field conflict is not merged: it waits for a human.
        if status == "merged":
            for source_id in plan.source_ids:
                if source_id != plan.canonical_id:
                    conn.execute("UPDATE people SET merged_into = ? WHERE id = ?",
                                 (plan.canonical_id, source_id))
    conn.commit()
    return merged, pending


def record_run(conn: sqlite3.Connection, stage: str, started_at: str, *,
               llm_calls: int, cache_hits: int, records_in: int,
               records_out: int, notes: str) -> None:
    conn.execute(
        """
        INSERT INTO pipeline_runs (stage, started_at, finished_at, llm_calls,
                                   cache_hits, records_in, records_out, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (stage, started_at, utc_now(), llm_calls, cache_hits, records_in,
         records_out, notes),
    )
    conn.commit()


def write_enrichment(conn: sqlite3.Connection, people: Sequence[EnrichedPerson],
                     *, provider: str, model: str) -> int:
    """Replace the enrichment layer. Raw rows are untouched, as always."""
    conn.execute("DELETE FROM enrichment")
    _insert_enrichment(conn, people, provider=provider, model=model)
    conn.commit()
    return len(people)


def upsert_enrichment(conn: sqlite3.Connection, people: Sequence[EnrichedPerson],
                      *, provider: str, model: str) -> int:
    """Add or replace rows without clearing the table, so a resumed run keeps
    the batches an earlier run already paid for."""
    _insert_enrichment(conn, people, provider=provider, model=model)
    conn.commit()
    return len(people)


def _insert_enrichment(conn: sqlite3.Connection, people: Sequence[EnrichedPerson],
                       *, provider: str, model: str) -> None:
    now = utc_now()
    conn.executemany(
        """
        INSERT OR REPLACE INTO enrichment (
            person_id, persona, seniority, company_stage, sector, geography,
            needs, offers, confidence, evidence, evidence_total, evidence_verified,
            evidence_unverified, low_confidence, provider, model, prompt_version,
            from_cache, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        [
            (p.person_id, p.persona, p.seniority, p.company_stage, p.sector,
             p.geography,
             json.dumps(p.needs, ensure_ascii=False),
             json.dumps(p.offers, ensure_ascii=False),
             p.confidence,
             json.dumps(p.evidence, ensure_ascii=False),
             p.evidence_total, p.evidence_verified,
             json.dumps(p.evidence_unverified, ensure_ascii=False),
             int(p.low_confidence), provider, model, p.prompt_version, now)
            for p in people
        ],
    )


def canonical_records(conn: sqlite3.Connection) -> list[NormalizedRecord]:
    """Records that survived deduplication. Merged-away duplicates are skipped:
    enriching a row that has been folded into another spends tokens on a record
    nothing will ever read."""
    rows = conn.execute(
        "SELECT * FROM people WHERE merged_into IS NULL ORDER BY id").fetchall()
    return [normalize_record(row) for row in rows]


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


def revert_merge(conn: sqlite3.Connection, canonical_person_id: str,
                 note: str = "reverted by operator") -> dict:
    """Undo a merge. Nothing was deleted, so nothing has to be reconstructed.

    Three effects, in this order:
      1. the merge group is stamped reverted_at and keeps its full history
      2. every source row has merged_into cleared, so the records stand alone
      3. the pair is recorded as a human `keep_both` decision

    Step 3 is what makes the reversal stick. Without it the next pipeline run
    would recompute the same verdict and merge them again, and an operator would
    have to keep undoing the same mistake.
    """
    group = conn.execute(
        "SELECT * FROM merge_groups WHERE canonical_person_id = ? AND reverted_at IS NULL",
        (canonical_person_id,)).fetchone()
    if group is None:
        raise ValueError(f"no active merge group for {canonical_person_id}")

    source_ids = json.loads(group["source_record_ids"])
    now = utc_now()

    conn.execute("UPDATE merge_groups SET status = 'reverted', reverted_at = ? WHERE id = ?",
                 (now, group["id"]))
    conn.execute(
        f"UPDATE people SET merged_into = NULL WHERE id IN ({','.join('?' * len(source_ids))})",
        source_ids)

    recorded = 0
    for a, b in ((x, y) for i, x in enumerate(source_ids) for y in source_ids[i + 1:]):
        pair = conn.execute(
            "SELECT id FROM duplicate_pairs WHERE person_a_id = ? AND person_b_id = ?",
            (min(a, b), max(a, b))).fetchone()
        if pair is None:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO duplicate_reviews (pair_id, decision, note, decided_at)
               VALUES (?, 'keep_both', ?, ?)""", (pair["id"], note, now))
        conn.execute("UPDATE duplicate_pairs SET review_state = 'rejected' WHERE id = ?",
                     (pair["id"],))
        recorded += 1

    conn.commit()
    return {"merge_group_id": group["id"], "source_record_ids": source_ids,
            "reverted_at": now, "reviews_recorded": recorded}


def human_decisions(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Pair -> the decision a human made about it. Survives recomputation."""
    rows = conn.execute(
        """SELECT p.person_a_id AS a, p.person_b_id AS b, r.decision
           FROM duplicate_reviews r JOIN duplicate_pairs p ON p.id = r.pair_id""")
    return {(row["a"], row["b"]): row["decision"] for row in rows}


def write_applicant_scores(conn: sqlite3.Connection, scored) -> int:
    """Persist the deterministic score and the prose written from it.

    The components are stored separately from the total on purpose: a reviewer
    who disagrees with a score needs to see which of the five moved it, and a
    single number cannot be argued with.
    """
    from backend.app.pipeline.scoring import unsupported_numbers

    now = utc_now()
    conn.execute("DELETE FROM applicant_scores")
    rows = []
    for applicant in scored:
        b = applicant.breakdown
        prose = f"{applicant.explanation} {' '.join(applicant.bullets)}"
        rows.append((
            b.person_id,
            b.component("persona_fit").points,
            b.component("seniority").points,
            b.component("company_stage").points,
            b.component("referral_signal").points,
            b.component("profile_signal").points,
            b.total, b.band,
            json.dumps([{"name": c.name, "points": c.points,
                         "out_of": c.max_points, "signal": c.signal,
                         "basis": c.basis} for c in b.components],
                       ensure_ascii=False),
            applicant.explanation,
            json.dumps(applicant.bullets, ensure_ascii=False),
            applicant.explanation_kind,
            json.dumps(sorted(unsupported_numbers(b, prose))),
            now,
        ))
    conn.executemany(
        """
        INSERT INTO applicant_scores (
            person_id, persona_fit, seniority, company_stage, referral_signal,
            profile_signal, total, band, signals, explanation, bullets,
            explanation_kind, unsupported_numbers, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
    conn.commit()
    return len(rows)


def write_embeddings(conn: sqlite3.Connection, vectors: dict, model: str) -> int:
    """Persist the profile vector per person.

    Only the profile vector: needs and offers are per-phrase and are cheap to
    recompute, while the profile vector is what a UI would want for a "people
    like this" panel without re-embedding the network.
    """
    now = utc_now()
    conn.execute("DELETE FROM embeddings")
    conn.executemany(
        """INSERT INTO embeddings (person_id, kind, vector, dim, model, created_at)
           VALUES (?, 'profile', ?, ?, ?, ?)""",
        [(person_id, v.profile_vector.astype("float32").tobytes(),
          int(v.profile_vector.shape[0]), model, now)
         for person_id, v in sorted(vectors.items())],
    )
    conn.commit()
    return len(vectors)


def write_introductions(conn: sqlite3.Connection, suggestions) -> int:
    """Persist suggestions as `suggested`. Nothing here is approved or sent.

    Decisions already made by a human are preserved: an approved or dismissed
    pair keeps its status rather than reverting to a fresh suggestion because
    the engine happened to re-run.
    """
    now = utc_now()
    decided = {(row["person_a_id"], row["person_b_id"]): row["status"]
               for row in conn.execute(
                   "SELECT * FROM introductions WHERE status != 'suggested'")}
    conn.execute("DELETE FROM introductions WHERE status = 'suggested'")

    rows = []
    for s in suggestions:
        key = (min(s.a_id, s.b_id), max(s.a_id, s.b_id))
        if key in decided:
            continue
        rows.append((s.a_id, s.b_id, s.score, s.complementarity, s.similarity,
                     s.matched_need, s.matched_offer, s.why, s.a_gets, s.b_gets,
                     s.draft_message, "suggested", now))
    conn.executemany(
        """INSERT OR REPLACE INTO introductions (
               person_a_id, person_b_id, score, complementarity, similarity,
               matched_need, matched_offer, why, a_gets, b_gets, draft_message,
               status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    conn.commit()
    return len(rows)
