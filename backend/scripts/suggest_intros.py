"""Build introduction suggestions, then have the model write the copy.

    python backend/scripts/suggest_intros.py --no-llm    # scoring + filters only
    python backend/scripts/suggest_intros.py --estimate  # what would the copy cost
    python backend/scripts/suggest_intros.py --resume    # write copy for what lacks it

Everything up to and including the safety filters is local: embeddings run on
CPU, the filters are Python, and no token is spent on a pair that was never
going to be suggested. The model is only asked to write, never to decide.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.app import config, db  # noqa: E402
from backend.app.llm import provider as llm  # noqa: E402
from backend.app.pipeline import intros, store  # noqa: E402


class IntroCopy(BaseModel):
    pair: int
    why: str
    a_gets: str
    b_gets: str
    draft_message: str = Field(description="under 120 words")


class IntroCopyBatch(BaseModel):
    intros: list[IntroCopy]


def build(conn, use_enrichment: bool = True):
    records = store.canonical_records(conn)
    enrichment = {}
    if use_enrichment:
        enrichment = {row["person_id"]: dict(row)
                      for row in conn.execute("SELECT * FROM enrichment")}

    from fastembed import TextEmbedding
    embedder = TextEmbedding(model_name=intros.CONFIG["EMBED_MODEL"])

    started = time.monotonic()
    vectors = intros.embed_people(records, embedder, enrichment)
    embed_seconds = time.monotonic() - started

    blocked = [(r["person_a_id"], r["person_b_id"])
               for r in conn.execute("SELECT * FROM blocked_pairs")]
    introduced = [(r["person_a_id"], r["person_b_id"])
                  for r in conn.execute(
                      "SELECT * FROM introductions WHERE status = 'approved'")]

    result = intros.build_suggestions(records, vectors, enrichment,
                                      blocked=blocked, introduced=introduced)
    result.funnel["embed_seconds"] = round(embed_seconds, 1)
    return records, vectors, result


def write_copy(provider, result, records, pace: float, resume: bool) -> int:
    by_id = {r.id: r for r in records}
    schema_json = llm.schema_hint(IntroCopyBatch)
    pending = [s for s in result.suggestions if not (resume and s.why)]
    size = intros.CONFIG["LLM_BATCH_SIZE"]
    calls = 0

    for start in range(0, len(pending), size):
        batch = pending[start:start + size]
        blocks = "\n\n".join(
            intros.render_pair(i, s, by_id[s.a_id], by_id[s.b_id])
            for i, s in enumerate(batch))
        prompt = intros.INTRO_PROMPT.format(schema=schema_json, pairs=blocks)

        if pace and calls:
            time.sleep(pace)
        try:
            response = provider.complete_json("intro_copy", prompt, IntroCopyBatch)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! batch {start // size} failed: "
                  f"{exc.__class__.__name__}: {str(exc)[:120]}", flush=True)
            continue
        calls += 1

        for item in response["intros"]:
            index = item.get("pair", -1)
            if 0 <= index < len(batch):
                suggestion = batch[index]
                suggestion.why = item["why"]
                suggestion.a_gets = item["a_gets"]
                suggestion.b_gets = item["b_gets"]
                suggestion.draft_message = item["draft_message"]
    return calls


def report(result, records, estimate: dict) -> None:
    f = result.funnel
    by_id = {r.id: r for r in records}

    print("\nINTRODUCTION ENGINE")
    print(f"  canonical people            {f['people']:>7,}")
    print(f"  actionable                  {f['actionable_people']:>7,}"
          f"   ({f['people_filtered_out']} below the completeness floor)")
    print(f"  pairs considered            {f['pairs_considered']:>7,}")
    print(f"  pairs scoring above floor   {f['pairs_scored']:>7,}")
    print(f"  suggestions (top 3 each)    {f['suggestions']:>7,}")
    print(f"  reciprocal                  {f['reciprocal']:>7,}"
          f"   ({f['reciprocal'] / max(1, f['suggestions']):.0%})")
    print(f"  people with a suggestion    {f['people_with_a_suggestion']:>7,}")
    print(f"  embedding time              {f['embed_seconds']:>7}s")

    print("\nREJECTED BY FILTER (deterministic, before any model call)")
    for name, count in sorted(result.rejected.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<22} {count:>8,}")

    print("\nTOKEN ESTIMATE FOR THE COPY")
    for key, value in estimate.items():
        print(f"  {key:<24} {value:>9,}" if isinstance(value, int)
              else f"  {key:<24} {value:>9}")

    print("\nTOP SUGGESTIONS")
    for suggestion in result.suggestions[:3]:
        a, b = by_id[suggestion.a_id], by_id[suggestion.b_id]
        print(f"\n  {suggestion.score:.3f}  {'RECIPROCAL' if suggestion.reciprocal else 'one-way'}"
              f"  (comp {suggestion.complementarity:.2f} / {suggestion.reverse_complementarity:.2f},"
              f" sim {suggestion.similarity:.2f})")
        print(f"    A  {a.raw_name} — {a.raw_title} @ {a.raw_company}")
        print(f"    B  {b.raw_name} — {b.raw_title} @ {b.raw_company}")
        print(f"    A needs : {suggestion.matched_need}")
        print(f"    B offers: {suggestion.matched_offer}")
        if suggestion.why:
            print(f"    why     : {suggestion.why}")
            print(f"    A gets  : {suggestion.a_gets}")
            print(f"    B gets  : {suggestion.b_gets}")
            print(f"    draft   : {suggestion.draft_message[:220]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--estimate", action="store_true",
                        help="build suggestions, print the cost, write nothing")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pace", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=None,
                        help="only write copy for the top N suggestions")
    args = parser.parse_args()

    if args.offline:
        config.LLM_OFFLINE = True

    conn = db.connect(args.db)
    records, vectors, result = build(conn)

    if args.limit:
        result.suggestions = result.suggestions[: args.limit]
        result.funnel["suggestions"] = len(result.suggestions)

    schema_chars = len(llm.schema_hint(IntroCopyBatch))
    estimate = intros.estimate_tokens(result.suggestions, schema_chars)

    if args.estimate:
        report(result, records, estimate)
        conn.close()
        return

    started = time.monotonic()
    if not args.no_llm:
        write_copy(llm.get_provider(), result, records, args.pace, args.resume)
    elapsed = time.monotonic() - started

    store.write_embeddings(conn, vectors, intros.CONFIG["EMBED_MODEL"])
    store.write_introductions(conn, result.suggestions)
    usage = llm.usage()
    store.record_run(conn, "introductions", db.utc_now(),
                     llm_calls=int(usage["llm_calls"]),
                     cache_hits=int(usage["cache_hits"]),
                     records_in=len(records),
                     records_out=len(result.suggestions),
                     notes=json.dumps(result.funnel))

    report(result, records, estimate)
    print(f"\nLLM  {json.dumps(usage)}")
    print(f"wall clock {elapsed:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
