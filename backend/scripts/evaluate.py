"""Evaluate the duplicate pipeline against ground truth.

    python backend/scripts/evaluate.py
    python backend/scripts/evaluate.py --json

This is the ONLY code permitted to read data/ground_truth.json. Nothing under
backend/app may, and test_no_answer_key.py enforces that.

Unresolved pairs are reported separately rather than counted as either a hit or
a miss. A pair that escalated to stage 3 and never got adjudicated is not a
pipeline failure -- it is a pipeline result that has not arrived yet, and
folding it into precision or recall would misattribute a missing API key to the
matcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import db  # noqa: E402
from backend.app.pipeline import dedupe, store  # noqa: E402

DEFAULT_GROUND_TRUTH = REPO_ROOT / "data" / "ground_truth.json"

Pair = tuple[str, str]


def norm(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_ground_truth(path: Path | None = None) -> dict:
    return json.loads((path or DEFAULT_GROUND_TRUTH).read_text(encoding="utf-8"))


def gt_positive_pairs(gt: dict) -> dict[Pair, str]:
    """Every within-cluster pair. A three-record cluster contributes three."""
    pairs: dict[Pair, str] = {}
    for cluster in gt["duplicate_clusters"]:
        for a, b in combinations(sorted(cluster["record_ids"]), 2):
            pairs[norm(a, b)] = cluster["cluster_id"]
    return pairs


def gt_near_miss_pairs(gt: dict) -> dict[Pair, str]:
    return {norm(*n["record_ids"]): n["kind"] for n in gt["near_miss_pairs"]}


def load_predictions(conn) -> tuple[dict[Pair, dict], dict[Pair, dict]]:
    """Return (asserted same_person pairs, unresolved pairs)."""
    asserted: dict[Pair, dict] = {}
    unresolved: dict[Pair, dict] = {}
    for row in conn.execute("SELECT * FROM duplicate_pairs"):
        pair = norm(row["person_a_id"], row["person_b_id"])
        info = {"stage": row["stage"], "method": row["method"], "score": row["score"],
                "verdict": row["verdict"], "review_state": row["review_state"]}
        if row["verdict"] == "same_person":
            asserted[pair] = info
        elif row["method"] == "escalated_not_adjudicated":
            unresolved[pair] = info
    return asserted, unresolved


def effective_pairs(conn) -> set[Pair]:
    """Pairs that are actually merged in the database, transitively.

    A cluster held back by a field conflict is NOT merged, so it does not
    appear here even though the pipeline asserted the match.
    """
    pairs: set[Pair] = set()
    for row in conn.execute("SELECT source_record_ids FROM merge_groups WHERE status = 'merged'"):
        ids = sorted(json.loads(row["source_record_ids"]))
        pairs.update(norm(a, b) for a, b in combinations(ids, 2))
    return pairs


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------


def diagnose(records) -> dict[Pair, dict]:
    """Recompute stage 0 and stage 2 so a miss can be explained, not just counted."""
    by_id = {r.id: r for r in records}
    candidates, _keys, _skipped = dedupe.build_candidates(records)

    diagnosis: dict[Pair, dict] = {}
    for (a_id, b_id), keys in candidates.items():
        a, b = by_id[a_id], by_id[b_id]
        method = dedupe.deterministic_verdict(a, b)
        if method:
            diagnosis[norm(a_id, b_id)] = {"outcome": "stage1_merged", "method": method,
                                           "score": 100.0, "keys": sorted(keys)}
            continue
        score, parts = dedupe.fuzzy_score(a, b)
        if score >= dedupe.CONFIG["AUTO_MERGE_AT"]:
            outcome = "stage2_auto_merged"
        elif score >= dedupe.CONFIG["ESCALATE_FLOOR"]:
            outcome = "stage2_escalated"
        else:
            outcome = "stage2_dropped"
        diagnosis[norm(a_id, b_id)] = {"outcome": outcome, "method": "fuzzy",
                                       "score": score, "parts": parts,
                                       "keys": sorted(keys)}
    return diagnosis


def explain_miss(pair: Pair, diagnosis: dict[Pair, dict], by_id) -> str:
    entry = diagnosis.get(pair)
    if entry is None:
        a, b = (by_id[i] for i in pair)
        shared = set(dedupe.blocking_keys(a)) & set(dedupe.blocking_keys(b))
        if not shared:
            return "never became a candidate: no shared blocking key"
        return "never became a candidate: blocking bucket too large"
    if entry["outcome"] == "stage2_dropped":
        parts = entry.get("parts", {})
        return (f"scored {entry['score']} < ESCALATE_FLOOR "
                f"(name {parts.get('name')}, company {parts.get('company')})")
    if entry["outcome"] == "stage2_escalated":
        return f"escalated at {entry['score']}, awaiting stage 3"
    return entry["outcome"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def score_pairs(truth: set[Pair], predicted: set[Pair], unresolved: set[Pair]) -> dict:
    considered = predicted - unresolved
    tp = sorted(considered & truth)
    fp = sorted(considered - truth)
    fn = sorted(truth - considered - unresolved)

    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 1.0
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "unresolved_truth": sorted(truth & unresolved),
        "unresolved_non_truth": sorted(unresolved - truth),
    }


def evaluate(conn, ground_truth_path: Path | None = None) -> dict:
    gt = load_ground_truth(ground_truth_path)
    records = store.load_records(conn)
    by_id = {r.id: r for r in records}

    truth = gt_positive_pairs(gt)
    near_misses = gt_near_miss_pairs(gt)
    asserted, unresolved = load_predictions(conn)
    merged_now = effective_pairs(conn)
    diagnosis = diagnose(records)

    asserted_metrics = score_pairs(set(truth), set(asserted), set(unresolved))
    effective_metrics = score_pairs(set(truth), merged_now, set(unresolved))

    # Where did each seeded near miss end up?
    near_miss_outcomes = []
    for pair, kind in sorted(near_misses.items()):
        entry = diagnosis.get(pair)
        outcome = entry["outcome"] if entry else "never_a_candidate"
        near_miss_outcomes.append({
            "pair": list(pair),
            "kind": kind,
            "names": [by_id[pair[0]].raw_name, by_id[pair[1]].raw_name],
            "companies": [by_id[pair[0]].raw_company, by_id[pair[1]].raw_company],
            "outcome": outcome,
            "score": entry["score"] if entry else None,
            "auto_merged": pair in asserted,
            "merged_in_db": pair in merged_now,
        })

    misses = [{"pair": list(pair), "cluster": truth[pair],
               "names": [by_id[pair[0]].raw_name, by_id[pair[1]].raw_name],
               "why": explain_miss(pair, diagnosis, by_id)}
              for pair in asserted_metrics["false_negatives"]]

    false_merges = [{"pair": list(pair),
                     "names": [by_id[pair[0]].raw_name, by_id[pair[1]].raw_name],
                     "companies": [by_id[pair[0]].raw_company, by_id[pair[1]].raw_company],
                     "is_seeded_near_miss": pair in near_misses,
                     "detail": asserted[pair]}
                    for pair in asserted_metrics["false_positives"]]

    rejected = sum(1 for n in near_miss_outcomes if not n["auto_merged"])

    return {
        "counts": {
            "records": len(records),
            "ground_truth_positive_pairs": len(truth),
            "ground_truth_clusters": len(gt["duplicate_clusters"]),
            "seeded_near_miss_pairs": len(near_misses),
            "asserted_same_person": len(asserted),
            "unresolved": len(unresolved),
        },
        "asserted": asserted_metrics,
        "effective": effective_metrics,
        "near_miss_rejection_accuracy": round(rejected / len(near_misses), 4) if near_misses else 1.0,
        "near_miss_outcomes": near_miss_outcomes,
        "missed_duplicates": misses,
        "false_merges": false_merges,
        "thresholds": {k: dedupe.CONFIG[k] for k in
                       ("AUTO_MERGE_AT", "ESCALATE_FLOOR", "NAME_ONLY_CAP",
                        "AUTO_MERGE_MIN_NAME")},
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render(report: dict) -> str:
    c, a, e = report["counts"], report["asserted"], report["effective"]
    out: list[str] = []

    out.append("DUPLICATE DETECTION")
    out.append(f"  ground-truth duplicate pairs   {c['ground_truth_positive_pairs']}"
               f" across {c['ground_truth_clusters']} clusters")
    out.append(f"  asserted same_person           {c['asserted_same_person']}")
    out.append(f"  unresolved (stage 3 not run)   {c['unresolved']}"
               f"  [{len(a['unresolved_truth'])} true dupes,"
               f" {len(a['unresolved_non_truth'])} non-dupes]")
    out.append("")
    out.append(f"  precision  {a['precision']:.4f}   "
               f"({len(a['true_positives'])} TP / {len(a['false_positives'])} FP)")
    out.append(f"  recall     {a['recall']:.4f}   "
               f"({len(a['true_positives'])} TP / {len(a['false_negatives'])} FN)")
    out.append(f"  f1         {a['f1']:.4f}")
    out.append("")
    out.append(f"  after the conflict gate (what is actually merged in the db):")
    out.append(f"  precision  {e['precision']:.4f}   recall {e['recall']:.4f}   "
               f"({len(e['true_positives'])} TP, {len(e['false_negatives'])} FN)")
    out.append("")
    out.append(f"  near-miss rejection accuracy   {report['near_miss_rejection_accuracy']:.4f}")

    out.append("")
    out.append("SEEDED NEAR MISSES (all 9 are genuinely different people)")
    for n in report["near_miss_outcomes"]:
        flag = "  <<< FALSE MERGE" if n["auto_merged"] else ""
        score = f"{n['score']:.2f}" if n["score"] is not None else "  -- "
        out.append(f"  {n['pair'][0]}/{n['pair'][1]}  {score:>6}  {n['outcome']:<20}"
                   f" {n['kind']}{flag}")
        out.append(f"      {n['names'][0]!r} @ {n['companies'][0]!r}")
        out.append(f"      {n['names'][1]!r} @ {n['companies'][1]!r}")

    out.append("")
    if report["false_merges"]:
        out.append(f"!! FALSE MERGES: {len(report['false_merges'])}")
        for f in report["false_merges"]:
            out.append(f"  {f['pair'][0]}/{f['pair'][1]}  {f['names']}  {f['companies']}")
            out.append(f"      seeded near miss: {f['is_seeded_near_miss']}  {f['detail']}")
    else:
        out.append("FALSE MERGES: none")

    out.append("")
    out.append(f"MISSED DUPLICATES: {len(report['missed_duplicates'])}")
    for m in report["missed_duplicates"]:
        out.append(f"  {m['pair'][0]}/{m['pair'][1]}  {m['cluster']}  {m['names']}")
        out.append(f"      {m['why']}")

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = db.connect(args.db)
    if not conn.execute("SELECT COUNT(*) FROM duplicate_pairs").fetchone()[0]:
        raise SystemExit("no duplicate_pairs -- run backend/scripts/run_pipeline.py first")

    report = evaluate(conn, args.ground_truth)
    conn.close()

    print(json.dumps(report, indent=2) if args.json else render(report))


if __name__ == "__main__":
    main()
