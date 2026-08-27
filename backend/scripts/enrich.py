"""Run AI enrichment over the canonical people.

    python backend/scripts/enrich.py                # everything not yet merged away
    python backend/scripts/enrich.py --limit 24     # a cheap first look
    python backend/scripts/enrich.py --offline      # cache only, no network

Prints what the model actually returned, including how much of its evidence
survived being checked against the records.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import config, db  # noqa: E402
from backend.app.llm import provider as llm  # noqa: E402
from backend.app.pipeline import enrich, store  # noqa: E402


def report(result: enrich.EnrichmentResult, records, elapsed: float,
           batch_size: int) -> None:
    f = result.funnel
    by_id = {r.id: r for r in records}

    print("\nENRICHMENT")
    print(f"  records            {f['records']:>5}")
    print(f"  batches            {f['batches']:>5}  (size {batch_size})")
    print(f"  returned           {f['returned']:>5}")
    print(f"  skipped by model   {f['missing']:>5}  -> stored as unknown, never guessed")

    print("\nCLASSIFICATIONS")
    for label, key in (("persona", "persona"), ("seniority", "seniority"),
                       ("company_stage", "company_stage"), ("sector", "sector"),
                       ("geography", "geography")):
        counts = Counter(getattr(p, key) for p in result.people)
        unknown = counts.get("unknown", 0)
        top = ", ".join(f"{k}={v}" for k, v in counts.most_common(6))
        print(f"  {label:<14} unknown={unknown:<4} {top}")

    print("\nCONFIDENCE")
    print(f"  low confidence (< {enrich.CONFIG['LOW_CONFIDENCE_AT']})  {f['low_confidence']}")
    if result.people:
        mean = sum(p.confidence for p in result.people) / len(result.people)
        print(f"  mean confidence               {mean:.3f}")

    print("\nEVIDENCE (every quote checked against the record it cites)")
    verified, total = f["evidence_verified"], f["evidence_total"]
    rate = verified / total if total else 1.0
    print(f"  quotes           {total}")
    print(f"  verified         {verified}  ({rate:.1%})")
    print(f"  records with at least one bad quote  {f['records_with_bad_evidence']}")

    bad = [p for p in result.people if p.evidence_unverified][:5]
    if bad:
        print("\n  examples of quotes that failed verification:")
        for person in bad:
            for item in person.evidence_unverified[:2]:
                actual = enrich.source_text(by_id[person.person_id], item.get("field"))
                print(f"    {person.person_id}  field={item.get('field')!r} "
                      f"quote={item.get('quote')!r}")
                print(f"        why: {item['why']}")
                print(f"        actual value: {str(actual)[:90]!r}")

    print("\nLLM")
    print(f"  {json.dumps(llm.usage())}")
    print(f"  wall clock  {elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--offline", action="store_true", help="cache only")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--pace", type=float, default=0.0,
                        help="seconds between batches, to stay under a TPM budget")
    parser.add_argument("--resume", action="store_true",
                        help="only enrich people with no usable enrichment yet")
    args = parser.parse_args()

    if args.offline:
        config.LLM_OFFLINE = True

    conn = db.connect(args.db)
    records = store.canonical_records(conn)
    if args.resume:
        done = {row[0] for row in conn.execute(
            "SELECT person_id FROM enrichment WHERE confidence > 0")}
        records = [r for r in records if r.id not in done]
        print(f"resume: {len(done)} already enriched, {len(records)} to go")
    if args.limit:
        records = records[: args.limit]
    if not records:
        raise SystemExit("no canonical people -- run the dedupe pipeline first")

    provider = llm.get_provider()
    print(f"provider={provider.name} model={provider.model} "
          f"prompt={enrich.PROMPT_VERSION} records={len(records)}")

    started = time.monotonic()
    result = enrich.run(
        records, provider, batch_size=args.batch_size, pace=args.pace,
        on_error=lambda i, e: print(f"  ! batch {i} failed: "
                                    f"{e.__class__.__name__}: {str(e)[:140]}",
                                    flush=True))
    elapsed = time.monotonic() - started

    if not args.dry_run:
        store.upsert_enrichment(conn, result.people,
                               provider=provider.name, model=provider.model)
        usage = llm.usage()
        store.record_run(conn, "enrichment", db.utc_now(),
                         llm_calls=int(usage["llm_calls"]),
                         cache_hits=int(usage["cache_hits"]),
                         records_in=len(records), records_out=len(result.people),
                         notes=json.dumps(result.funnel))

    report(result, records, elapsed, args.batch_size or enrich.CONFIG['BATCH_SIZE'])
    conn.close()


if __name__ == "__main__":
    main()
