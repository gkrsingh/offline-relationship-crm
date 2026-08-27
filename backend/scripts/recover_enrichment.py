"""Rebuild the enrichment table from the LLM cache on disk.

    python backend/scripts/recover_enrichment.py

Why this exists: the cache is keyed by the *batch* prompt, so any change to the
set of canonical people re-chunks the batches and every key misses, even though
the answers for those individual people are sitting on disk. That is a bad cache
key for a batched stage, and `enrich.py` now also caches per person. This script
recovers what the batch-keyed era produced: it reads every cached response, takes
the per-person objects out of them, and writes them straight into the table.

Idempotent, and never touches the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import config, db  # noqa: E402
from backend.app.pipeline import enrich, store  # noqa: E402


def harvest(cache_dir: Path) -> dict[str, dict]:
    """Every person object in every cached enrichment response, newest wins."""
    found: dict[str, dict] = {}
    files = sorted((cache_dir / "enrichment").glob("*.json"),
                   key=lambda p: p.stat().st_mtime)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for person in payload.get("response", {}).get("people", []):
            if isinstance(person, dict) and person.get("person_id"):
                found[person["person_id"]] = person
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--cache", type=Path, default=config.LLM_CACHE_DIR)
    args = parser.parse_args()

    harvested = harvest(args.cache)
    print(f"cache holds enrichments for {len(harvested)} distinct people")

    conn = db.connect(args.db)
    records = {r.id: r for r in store.canonical_records(conn)}
    print(f"canonical people in the database: {len(records)}")

    people = []
    verified = total = 0
    for person_id, record in sorted(records.items()):
        payload = harvested.get(person_id)
        if payload is None:
            people.append(enrich.unknown_enrichment(
                person_id, "not present in the cache"))
            continue
        try:
            answer = enrich.PersonEnrichment(**payload)
        except Exception:  # noqa: BLE001 -- a stale schema is a miss, not a crash
            people.append(enrich.unknown_enrichment(
                person_id, "cached response no longer matches the schema"))
            continue

        evidence = [e.model_dump() for e in answer.evidence]
        check = enrich.verify_evidence(record, evidence)
        verified += check.verified
        total += check.total

        people.append(enrich.EnrichedPerson(
            person_id=person_id, persona=answer.persona,
            seniority=answer.seniority, company_stage=answer.company_stage,
            sector=answer.sector, geography=answer.geography,
            needs=list(answer.needs), offers=list(answer.offers),
            confidence=answer.confidence, evidence=evidence,
            evidence_verified=check.verified, evidence_total=check.total,
            evidence_unverified=check.unverified,
            low_confidence=answer.confidence < enrich.CONFIG["LOW_CONFIDENCE_AT"],
        ))

    store.write_enrichment(conn, people, provider="groq",
                           model=config.GROQ_MODEL)

    usable = sum(1 for p in people if p.confidence > 0)
    print(f"written: {len(people)} rows, {usable} usable "
          f"({usable / len(people):.0%} coverage)")
    print(f"evidence verified: {verified}/{total} "
          f"({verified / total:.1%})" if total else "no evidence")
    conn.close()


if __name__ == "__main__":
    main()
