"""Run normalization + the duplicate pipeline against data/crm.db.

    python backend/scripts/run_pipeline.py                # full run, uses the LLM if configured
    python backend/scripts/run_pipeline.py --no-llm       # stages 0-2 only
    python backend/scripts/run_pipeline.py --dry-run      # compute, print, write nothing

Prints the funnel, because "how many pairs reached the model" is the number that
says whether the cheap stages are doing their job.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import db  # noqa: E402
from backend.app.llm import provider as llm  # noqa: E402
from backend.app.pipeline import dedupe, merge, store  # noqa: E402


def build_merge_plans(records, pairs, decisions=None) -> list[merge.MergePlan]:
    """Cluster the auto-merge edges and run survivorship on each cluster.

    A pair a human has marked `keep_both` is dropped from the edge set, so a
    reverted merge stays reverted however many times the pipeline is re-run.
    """
    decisions = decisions or {}
    by_id = {r.id: r for r in records}

    overruled = 0
    merge_edges = []
    for p in pairs:
        if not (p.verdict == "same_person" and p.review_state == "auto_merged"):
            continue
        if decisions.get((min(p.a_id, p.b_id), max(p.a_id, p.b_id))) == "keep_both":
            p.review_state = "rejected"
            p.reason = f"{p.reason} [overruled by an operator: keep_both]"
            overruled += 1
            continue
        merge_edges.append((p.a_id, p.b_id))

    if overruled:
        print(f"  {overruled} pair(s) not merged: an operator already said keep_both")

    stage_by_pair = {(min(p.a_id, p.b_id), max(p.a_id, p.b_id)): p.stage for p in pairs}

    plans: list[merge.MergePlan] = []
    for cluster in merge.cluster_ids(merge_edges):
        stages = {stage_by_pair.get((min(a, b), max(a, b)))
                  for a in cluster for b in cluster if a < b}
        stages.discard(None)
        decided_by = ("stage1_exact" if stages == {"deterministic"}
                      else "llm" if "llm" in stages
                      else "stage2_fuzzy" if "fuzzy" in stages
                      else "mixed")
        plans.append(merge.plan_merge([by_id[i] for i in cluster], decided_by=decided_by))
    return plans


def print_funnel(result: dedupe.DedupeResult, plans: list[merge.MergePlan],
                 records) -> None:
    f = result.funnel
    kept = f["possible_pairs"] - f["candidate_pairs"]
    pct = 100 * kept / f["possible_pairs"] if f["possible_pairs"] else 0

    print("\nFUNNEL")
    print(f"  records                        {f['records']:>7,}")
    print(f"  possible pairs (n choose 2)    {f['possible_pairs']:>7,}")
    print(f"  stage 0  candidate pairs       {f['candidate_pairs']:>7,}   "
          f"({pct:.2f}% of pairs never compared)")
    for key, count in sorted(result.blocking_keys.items(), key=lambda kv: -kv[1]):
        label = {"li": "linkedin slug", "em": "email local-part",
                 "co": "company + last name", "nl": "surname prefix + city",
                 "nm": "first name only"}[key]
        print(f"             via {label:<24} {count:>6,}")
    if f["blocking_buckets_skipped"]:
        print(f"             oversized buckets skipped  {f['blocking_buckets_skipped']}")

    print(f"  stage 1  exact id -> merged    {f['stage1_merged']:>7,}")
    print(f"  stage 2  scored                {f['stage2_scored']:>7,}")
    print(f"             >= {dedupe.CONFIG['AUTO_MERGE_AT']} auto-merged        "
          f"{f['stage2_auto_merged']:>7,}")
    print(f"             {dedupe.CONFIG['ESCALATE_FLOOR']}-{dedupe.CONFIG['AUTO_MERGE_AT']} escalated       "
          f"{f['stage2_escalated']:>7,}")
    print(f"             < {dedupe.CONFIG['ESCALATE_FLOOR']} dropped            "
          f"{f['stage2_dropped']:>7,}")
    print(f"  stage 3  pairs adjudicated     {f['stage3_pairs']:>7,}   "
          f"in {f['stage3_batches']} batch(es)")
    print(f"             same_person               {f['stage3_same_person']:>7,}")
    print(f"             different_people          {f['stage3_different_people']:>7,}")
    print(f"             insufficient_evidence     {f['stage3_insufficient_evidence']:>7,}")
    if f["stage3_unadjudicated"]:
        print(f"             NOT adjudicated (no key)  {f['stage3_unadjudicated']:>7,}")

    pending = result.by_state("pending")
    auto = result.by_state("auto_merged")
    rejected = result.by_state("rejected")
    conflicted = [p for p in plans if p.requires_review]

    print("\nREVIEW QUEUE")
    print(f"  auto-merged (shown for undo)   {len(auto):>7,}")
    print(f"  pending a human decision       {len(pending):>7,}")
    print(f"  rejected as different people   {len(rejected):>7,}")
    print(f"  merge clusters                 {len(plans):>7,}")
    print(f"  clusters held on conflict      {len(conflicted):>7,}")

    if conflicted:
        print("\n  field conflicts:")
        for plan in conflicted:
            for conflict in plan.conflicts:
                print(f"    {'+'.join(plan.source_ids)}  {conflict.field}: "
                      f"{' vs '.join(conflict.values)}  ({conflict.detail})")

    blocked = [r for r in records if r.completeness.blocked]
    buckets = {"1.00": 0, "0.80-0.99": 0, "0.60-0.79": 0, "0.40-0.59": 0, "< 0.40": 0}
    for record in records:
        s = record.completeness.score
        key = ("1.00" if s >= 1.0 else "0.80-0.99" if s >= 0.8
               else "0.60-0.79" if s >= 0.6 else "0.40-0.59" if s >= 0.4 else "< 0.40")
        buckets[key] += 1

    print("\nCOMPLETENESS")
    for label, count in buckets.items():
        bar = "#" * round(40 * count / max(1, len(records)))
        print(f"  {label:<10} {count:>4}  {bar}")
    print(f"  blocked (no email or no company) {len(blocked)} of {len(records)}")

    print("\nLLM")
    usage = llm.usage()
    print(f"  calls {usage['llm_calls']}   cache hits {usage['cache_hits']}   "
          f"hit rate {usage['cache_hit_rate']:.0%}   budget {llm.MAX_LLM_CALLS}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--no-llm", action="store_true",
                        help="run stages 0-2 and leave escalated pairs pending")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--json", action="store_true", help="print the funnel as JSON")
    args = parser.parse_args()

    started = db.utc_now()
    conn = db.connect(args.db)
    db.apply_schema(conn)

    records = store.load_records(conn)
    if not records:
        raise SystemExit("no people in the database -- run backend/scripts/init_db.py first")

    provider = None
    if not args.no_llm:
        try:
            provider = llm.get_provider()
        except Exception as exc:  # noqa: BLE001 -- surfaced, not swallowed
            print(f"! no LLM provider available ({exc.__class__.__name__}: {exc})")
            print("! running stages 0-2 only; escalated pairs will stay pending\n")

    decisions = store.human_decisions(conn)
    result = dedupe.run(records, provider)
    plans = build_merge_plans(records, result.pairs, decisions)

    if not args.dry_run:
        store.write_normalized(conn, records)
        store.write_pairs(conn, result.pairs)
        store.write_merges(conn, plans)
        collapsed = sum(len(p.source_ids) - 1 for p in plans if not p.requires_review)
        usage = llm.usage()
        store.record_run(
            conn, "normalize+dedupe", started,
            llm_calls=int(usage["llm_calls"]), cache_hits=int(usage["cache_hits"]),
            records_in=len(records), records_out=len(records) - collapsed,
            notes=json.dumps(result.funnel),
        )

    if args.json:
        print(json.dumps({"funnel": result.funnel,
                          "blocking_keys": result.blocking_keys,
                          "llm": llm.usage()}, indent=2))
    else:
        print_funnel(result, plans, records)

    conn.close()


if __name__ == "__main__":
    main()
