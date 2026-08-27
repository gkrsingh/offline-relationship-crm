"""Score membership applicants and have the model write the reviewer note.

    python backend/scripts/score_applicants.py
    python backend/scripts/score_applicants.py --no-llm     # numbers only
    python backend/scripts/score_applicants.py --offline    # cache only

The scores are computed before the model is contacted, and the model is handed
the breakdown alone. Nothing it returns can change a number.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import config, db  # noqa: E402
from backend.app.llm import provider as llm  # noqa: E402
from backend.app.pipeline import scoring, store  # noqa: E402

BATCH_SIZE = 5


class Explanation(BaseModel):
    person_id: str
    summary: str = Field(description="exactly two sentences")
    bullets: list[str] = Field(min_length=3, max_length=4)


class ExplanationBatch(BaseModel):
    explanations: list[Explanation]


def load_inputs(conn) -> tuple[list, dict, dict, set[str]]:
    applications = {row["person_id"]: dict(row)
                    for row in conn.execute("SELECT * FROM applications")}
    enrichment = {row["person_id"]: dict(row)
                  for row in conn.execute("SELECT * FROM enrichment")}
    completeness = {row["person_id"]: row["completeness"]
                    for row in conn.execute(
                        "SELECT person_id, completeness FROM people_normalized")}
    # A referral only counts if it points at somebody who is actually here and
    # is not themselves an applicant.
    member_ids = {row["id"] for row in conn.execute(
        "SELECT id FROM people WHERE merged_into IS NULL AND source != 'applicant_form'")}
    return applications, enrichment, completeness, member_ids


def score_all(conn) -> list[scoring.ScoredApplicant]:
    applications, enrichment, completeness, member_ids = load_inputs(conn)
    scored = []
    for person_id in sorted(applications):
        breakdown = scoring.score_applicant(
            person_id=person_id,
            enrichment=enrichment.get(person_id),
            application=applications[person_id],
            completeness=completeness.get(person_id, 0.0),
            member_ids=member_ids,
        )
        scored.append(scoring.ScoredApplicant(
            breakdown=breakdown, explanation_kind=breakdown.kind))
    return scored


def explain(provider, scored: list[scoring.ScoredApplicant], pace: float) -> int:
    """One call per batch, batches never mixing review notes with declines."""
    from backend.app.llm import cache

    schema_json = llm.schema_hint(ExplanationBatch)
    calls = 0

    # Per-applicant cache first; only genuinely new breakdowns cost a call.
    for applicant in scored:
        hit = cache.load(scoring.EXPLANATION_TASK,
                         scoring.explanation_cache_key(provider, applicant.breakdown))
        if hit:
            applicant.explanation = hit.get("summary", "")
            applicant.bullets = list(hit.get("bullets", []))

    for kind in ("why", "why_not"):
        group = [a for a in scored
                 if a.explanation_kind == kind and not a.explanation]
        for start in range(0, len(group), BATCH_SIZE):
            batch = group[start:start + BATCH_SIZE]
            prompt = scoring.build_batch_explanation_prompt(
                [a.breakdown for a in batch], schema_json)
            if pace and calls:
                time.sleep(pace)
            response = provider.complete_json("applicant_explanation", prompt,
                                              ExplanationBatch)
            calls += 1
            by_id = {e["person_id"]: e for e in response["explanations"]}
            for applicant in batch:
                answer = by_id.get(applicant.person_id)
                if answer:
                    applicant.explanation = answer["summary"]
                    applicant.bullets = list(answer["bullets"])
                    cache.store(
                        scoring.EXPLANATION_TASK,
                        scoring.explanation_cache_key(provider, applicant.breakdown),
                        provider=provider.name, model=provider.model,
                        request={"person_id": applicant.person_id},
                        response={"summary": applicant.explanation,
                                  "bullets": applicant.bullets})
    return calls


def report(scored: list[scoring.ScoredApplicant], elapsed: float) -> None:
    summary = scoring.summarise(scored)
    print("\nAPPLICANT SCORING")
    print(f"  applicants   {summary['applicants']}")
    print(f"  bands        strong={summary['bands']['strong']} "
          f"review={summary['bands']['review']} weak={summary['bands']['weak']}")
    print(f"  explained    {summary['explained']}  "
          f"(of which declines: {summary['why_not']})")

    offenders = [(a.person_id, sorted(scoring.unsupported_numbers(
        a.breakdown, a.explanation + " " + " ".join(a.bullets))))
        for a in scored if a.explanation]
    bad = [(pid, nums) for pid, nums in offenders if nums]
    print(f"\n  explanations citing a number not in their breakdown: {len(bad)}")
    for pid, nums in bad[:5]:
        print(f"    {pid}: {nums}")

    for kind, label in (("why", "REVIEW NOTE"), ("why_not", "DECLINE")):
        example = next((a for a in scored
                        if a.explanation_kind == kind and a.explanation), None)
        if not example:
            continue
        b = example.breakdown
        print(f"\n{label}  {b.person_id}  {b.total}/100  band={b.band}")
        for c in b.components:
            print(f"    {c.name:<16} {c.points:>5} / {c.max_points:<3} {c.signal}")
        print(f"    ---")
        print(f"    {example.explanation}")
        for bullet in example.bullets:
            print(f"      - {bullet}")

    print(f"\nLLM  {json.dumps(llm.usage())}")
    print(f"wall clock {elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--no-llm", action="store_true", help="numbers only")
    parser.add_argument("--offline", action="store_true", help="cache only")
    parser.add_argument("--pace", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.offline:
        config.LLM_OFFLINE = True

    conn = db.connect(args.db)
    scored = score_all(conn)
    if not scored:
        raise SystemExit("no applications found -- run init_db first")

    started = time.monotonic()
    if not args.no_llm:
        explain(llm.get_provider(), scored, args.pace)
    elapsed = time.monotonic() - started

    if not args.dry_run:
        store.write_applicant_scores(conn, scored)

    report(scored, elapsed)
    conn.close()


if __name__ == "__main__":
    main()
