"""Backfill everything the model owes, in the order the UI needs it.

    python backend/scripts/backfill.py
    python backend/scripts/backfill.py --stages 1,2

Five stages, most-visible-first. Applicants come before the rest of the network
because an applicant scored without enrichment reads as broken rather than
incomplete -- persona, seniority and stage all come back `unknown` and the whole
view looks wrong. Intro copy comes last because a suggestion without prose is
still a usable suggestion; a score without its inputs is not.

Every stage is resumable, every response is cached per person, and a quota
failure stops the run cleanly and says which stage it died in. Nothing already
paid for is lost.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import db  # noqa: E402
from backend.app.llm import provider as llm  # noqa: E402
from backend.app.llm.provider import LLMError  # noqa: E402
from backend.app.pipeline import enrich, intros, scoring, store  # noqa: E402
from backend.scripts.score_applicants import (  # noqa: E402
    ExplanationBatch, explain, score_all)
from backend.scripts.suggest_intros import IntroCopyBatch, build, write_copy  # noqa: E402

BATCH = 8


def coverage(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "canonical": q("SELECT COUNT(*) FROM people WHERE merged_into IS NULL"),
        "enriched": q("SELECT COUNT(*) FROM enrichment WHERE confidence > 0"),
        "applicants": q("SELECT COUNT(*) FROM applications"),
        "applicants_enriched": q(
            "SELECT COUNT(*) FROM enrichment e JOIN applications a USING(person_id)"
            " WHERE e.confidence > 0"),
        "scores": q("SELECT COUNT(*) FROM applicant_scores"),
        "scores_explained": q(
            "SELECT COUNT(*) FROM applicant_scores WHERE explanation IS NOT NULL"
            " AND explanation != ''"),
        "introductions": q("SELECT COUNT(*) FROM introductions"),
        "intros_with_copy": q(
            "SELECT COUNT(*) FROM introductions WHERE why IS NOT NULL AND why != ''"),
    }


def report_coverage(conn, label: str) -> None:
    c = coverage(conn)
    print(f"\n--- coverage after {label} ---", flush=True)
    print(f"  enrichment           {c['enriched']:>4} / {c['canonical']} canonical "
          f"({c['enriched'] / max(1, c['canonical']):.0%})", flush=True)
    print(f"  applicants enriched  {c['applicants_enriched']:>4} / {c['applicants']}",
          flush=True)
    print(f"  scores explained     {c['scores_explained']:>4} / {c['scores']}", flush=True)
    print(f"  intros with copy     {c['intros_with_copy']:>4} / {c['introductions']}",
          flush=True)
    print(f"  llm {json.dumps(llm.usage())}", flush=True)


def enrich_ids(conn, provider, ids: list[str], label: str) -> None:
    done = {r[0] for r in conn.execute(
        "SELECT person_id FROM enrichment WHERE confidence > 0")}
    records = [r for r in store.canonical_records(conn)
               if r.id in set(ids) and r.id not in done]
    if not records:
        print(f"  {label}: nothing to do", flush=True)
        return
    print(f"  {label}: {len(records)} people, batch {BATCH}", flush=True)
    result = enrich.run(records, provider, batch_size=BATCH,
                        on_error=lambda i, e: print(f"    ! batch {i}: {str(e)[:110]}",
                                                    flush=True))
    store.upsert_enrichment(conn, result.people,
                            provider=provider.name, model=provider.model)
    print(f"  {label}: {result.funnel['returned']} returned, "
          f"{result.funnel['missing']} missing", flush=True)


def stage_1(conn, provider) -> None:
    ids = [r[0] for r in conn.execute("SELECT person_id FROM applications")]
    enrich_ids(conn, provider, ids, "applicants")


def stage_2(conn, provider) -> None:
    scored = score_all(conn)
    print(f"  explaining {len(scored)} applicant scores", flush=True)
    explain(provider, scored, pace=0.0)
    store.write_applicant_scores(conn, scored)
    bad = [a.person_id for a in scored
           if a.explanation and scoring.unsupported_numbers(
               a.breakdown, a.explanation + " " + " ".join(a.bullets))]
    print(f"  explanations citing an unsupported number: {len(bad)}", flush=True)


def stage_3(conn, provider) -> None:
    ids = set()
    for row in conn.execute("SELECT person_a_id, person_b_id FROM introductions"):
        ids.update((row[0], row[1]))
    enrich_ids(conn, provider, sorted(ids), "people in a suggestion")


def stage_4(conn, provider) -> None:
    ids = [r.id for r in store.canonical_records(conn)]
    enrich_ids(conn, provider, ids, "everyone else")


def stage_5(conn, provider) -> None:
    records, vectors, result = build(conn)
    have = {(r[0], r[1]) for r in conn.execute(
        "SELECT person_a_id, person_b_id FROM introductions"
        " WHERE why IS NOT NULL AND why != ''")}
    for suggestion in result.suggestions:
        if (suggestion.a_id, suggestion.b_id) in have:
            suggestion.why = "cached"
    pending = [s for s in result.suggestions if s.why != "cached"]
    print(f"  intro copy: {len(pending)} of {len(result.suggestions)} need writing",
          flush=True)
    for s in result.suggestions:
        if s.why == "cached":
            s.why = ""
    write_copy(provider, result, records, pace=0.0, resume=False)
    store.write_embeddings(conn, vectors, intros.CONFIG["EMBED_MODEL"])
    store.write_introductions(conn, result.suggestions)


STAGES = {
    1: ("applicant enrichment", stage_1),
    2: ("applicant explanations", stage_2),
    3: ("enrichment for people in suggestions", stage_3),
    4: ("enrichment for everyone else", stage_4),
    5: ("introduction copy", stage_5),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--stages", default="1,2,3,4,5")
    args = parser.parse_args()

    wanted = [int(x) for x in args.stages.split(",") if x.strip()]
    conn = db.connect(args.db)
    provider = llm.get_provider()
    print(f"provider={provider.name} model={provider.model}", flush=True)
    report_coverage(conn, "start")

    started = time.monotonic()
    for number in wanted:
        label, run = STAGES[number]
        print(f"\n=== STAGE {number}: {label} ===", flush=True)
        try:
            run(conn, provider)
        except LLMError as exc:
            print(f"\n!! STOPPED in stage {number} ({label})", flush=True)
            print(f"!! {str(exc)[:400]}", flush=True)
            report_coverage(conn, f"stage {number} (incomplete)")
            print(f"\nresume with: python backend/scripts/backfill.py "
                  f"--stages {','.join(str(n) for n in wanted[wanted.index(number):])}",
                  flush=True)
            conn.close()
            raise SystemExit(1)
        report_coverage(conn, f"stage {number}: {label}")

    print(f"\nbackfill complete in {time.monotonic() - started:.0f}s", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
