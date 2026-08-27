"""Held-out evaluation: run the tuned pipeline against a dataset it has never seen.

    python backend/scripts/holdout.py
    python backend/scripts/holdout.py --seed 2027 --no-llm

Every threshold in dedupe.CONFIG was chosen by looking at draw A (seed 42), the
dataset in data/. Precision and recall measured on draw A are therefore a fit,
not a result: they say the rules describe the data they were derived from.

This script generates draw B with a different seed and the identical noise plan,
runs the pipeline with EVERY THRESHOLD UNCHANGED, and reports both draws side by
side. Nothing here may touch data/ -- draw B lives in its own directory with its
own database.

If draw B regresses, that is the finding. Retuning against it would just move
the overfit from one dataset to two.
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
from backend.app.pipeline import dedupe, store  # noqa: E402
from backend.scripts import evaluate as evaluator  # noqa: E402
from backend.scripts import generate_data  # noqa: E402
from backend.scripts.run_pipeline import build_merge_plans  # noqa: E402

TUNED_SEED = 42
DEFAULT_HOLDOUT_SEED = 2027
DEFAULT_OUT = REPO_ROOT / "data" / "holdout"


def build_holdout(seed: int, out_dir: Path) -> tuple[Path, Path]:
    """Generate draw B and load it into its own database."""
    records, applications, ground_truth = generate_data.generate(
        seed, generate_data.DEFAULT_CANONICAL)

    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "people_raw.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (raw_dir / "applications.json").write_text(
        json.dumps(applications, indent=2, ensure_ascii=False), encoding="utf-8")
    gt_path = out_dir / "ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2, ensure_ascii=False),
                       encoding="utf-8")

    db_path = out_dir / "crm.db"
    if db_path.exists():
        db_path.unlink()
    conn = db.connect(db_path)
    db.apply_schema(conn)
    db.load_people(conn, records)
    db.load_applications(conn, applications)
    conn.close()
    return db_path, gt_path


def run_pipeline_on(db_path: Path, use_llm: bool) -> dict:
    conn = db.connect(db_path)
    records = store.load_records(conn)

    provider = None
    if use_llm:
        try:
            provider = llm.get_provider()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! no LLM provider ({exc.__class__.__name__}); stages 0-2 only")

    result = dedupe.run(records, provider)
    plans = build_merge_plans(records, result.pairs, store.human_decisions(conn))
    store.write_normalized(conn, records)
    store.write_pairs(conn, result.pairs)
    store.write_merges(conn, plans)
    conn.close()
    return result.funnel


def summarise(report: dict, funnel: dict) -> dict:
    asserted = report["asserted"]
    near = report["near_miss_outcomes"]
    return {
        "records": funnel["records"],
        "candidate_pairs": funnel["candidate_pairs"],
        "stage1_merged": funnel["stage1_merged"],
        "stage2_auto_merged": funnel["stage2_auto_merged"],
        "stage2_title_vetoed": funnel.get("stage2_title_vetoed", 0),
        "stage2_escalated": funnel["stage2_escalated"],
        "stage2_dropped": funnel["stage2_dropped"],
        "stage3_same_person": funnel["stage3_same_person"],
        "stage3_different_people": funnel["stage3_different_people"],
        "stage3_insufficient": funnel["stage3_insufficient_evidence"],
        "gt_pairs": report["counts"]["ground_truth_positive_pairs"],
        "precision": asserted["precision"],
        "recall": asserted["recall"],
        "f1": asserted["f1"],
        "false_merges": len(report["false_merges"]),
        "missed": len(report["false_negatives"]) if "false_negatives" in report
                  else len(asserted["false_negatives"]),
        "near_miss_rejection": report["near_miss_rejection_accuracy"],
        "near_misses_merged": sum(1 for n in near if n["auto_merged"]),
    }


ROWS = [
    ("records", "records"),
    ("candidate pairs", "candidate_pairs"),
    ("stage 1 merged", "stage1_merged"),
    ("stage 2 auto-merged", "stage2_auto_merged"),
    ("  of which title-vetoed", "stage2_title_vetoed"),
    ("stage 2 escalated", "stage2_escalated"),
    ("stage 2 dropped", "stage2_dropped"),
    ("stage 3 same_person", "stage3_same_person"),
    ("stage 3 different_people", "stage3_different_people"),
    ("stage 3 insufficient", "stage3_insufficient"),
    ("ground-truth pairs", "gt_pairs"),
    ("PRECISION", "precision"),
    ("RECALL", "recall"),
    ("f1", "f1"),
    ("false merges", "false_merges"),
    ("missed duplicates", "missed"),
    ("near-miss rejection", "near_miss_rejection"),
    ("near-misses merged", "near_misses_merged"),
]


def render(tuned: dict, held: dict, seed: int) -> str:
    out = [
        "",
        f"{'':28} {'draw A (tuned)':>16} {'draw B (held out)':>18}",
        f"{'':28} {'seed ' + str(TUNED_SEED):>16} {'seed ' + str(seed):>18}",
        "-" * 66,
    ]
    for label, key in ROWS:
        a, b = tuned.get(key), held.get(key)
        marker = ""
        if isinstance(a, float) and isinstance(b, float) and b < a - 1e-9:
            marker = "   <- REGRESSION"
        elif key in ("false_merges", "missed", "near_misses_merged") and b > a:
            marker = "   <- REGRESSION"
        fmt = (lambda v: f"{v:.4f}") if isinstance(a, float) else (lambda v: f"{v}")
        out.append(f"{label:28} {fmt(a):>16} {fmt(b):>18}{marker}")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_HOLDOUT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    print(f"thresholds (unchanged, tuned on draw A): "
          f"{json.dumps({k: dedupe.CONFIG[k] for k in ('AUTO_MERGE_AT', 'ESCALATE_FLOOR', 'NAME_ONLY_CAP', 'AUTO_MERGE_MIN_NAME')})}")

    print(f"\ndraw A: evaluating the shipped dataset in data/")
    conn = db.connect(db.DEFAULT_DB_PATH)
    tuned_funnel = json.loads(conn.execute(
        "SELECT notes FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()["notes"])
    tuned_report = evaluator.evaluate(conn, evaluator.DEFAULT_GROUND_TRUTH)
    conn.close()

    print(f"draw B: generating seed {args.seed} into {args.out}")
    db_path, gt_path = build_holdout(args.seed, args.out)

    print("draw B: running the pipeline, no thresholds touched")
    llm.reset_usage()
    held_funnel = run_pipeline_on(db_path, use_llm=not args.no_llm)

    conn = db.connect(db_path)
    held_report = evaluator.evaluate(conn, gt_path)
    conn.close()

    tuned = summarise(tuned_report, tuned_funnel)
    held = summarise(held_report, held_funnel)

    if args.json:
        print(json.dumps({"tuned": tuned, "held_out": held,
                          "llm": llm.usage()}, indent=2))
        return

    print(render(tuned, held, args.seed))
    print(f"\ndraw B LLM usage: {json.dumps(llm.usage())}")

    if held_report["false_merges"]:
        print(f"\nFALSE MERGES on the held-out draw ({len(held_report['false_merges'])}):")
        for f in held_report["false_merges"]:
            print(f"  {f['pair'][0]}/{f['pair'][1]}  {f['names']}  {f['companies']}")
            print(f"      seeded near miss: {f['is_seeded_near_miss']}  {f['detail']}")

    if held_report["missed_duplicates"]:
        print(f"\nMISSED DUPLICATES on the held-out draw "
              f"({len(held_report['missed_duplicates'])}):")
        for m in held_report["missed_duplicates"]:
            print(f"  {m['pair'][0]}/{m['pair'][1]}  {m['names']}")
            print(f"      {m['why']}")

    print("\nnear misses on the held-out draw:")
    for n in held_report["near_miss_outcomes"]:
        flag = "  <<< FALSE MERGE" if n["auto_merged"] else ""
        score = f"{n['score']:.2f}" if n["score"] is not None else "  -- "
        print(f"  {score:>6}  {n['outcome']:<20} {n['kind']}{flag}")


if __name__ == "__main__":
    main()
