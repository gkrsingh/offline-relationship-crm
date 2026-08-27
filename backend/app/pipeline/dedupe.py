"""Duplicate detection: blocking, deterministic match, fuzzy scoring, LLM adjudication.

The shape of this file is the argument. Each stage is strictly cheaper and more
certain than the one after it, and each one's job is to hand as little as
possible to the next:

    stage 0  blocking          44,551 possible pairs -> a few hundred candidates
    stage 1  exact identifiers zero judgment, zero cost, auto-merge
    stage 2  RapidFuzz         cheap scoring, three-way split on thresholds
    stage 3  LLM               only the genuinely ambiguous residue, may abstain

Every threshold lives in CONFIG. Nothing downstream hard-codes a number.

Precision beats recall throughout, because the failure modes are not symmetric:
a missed duplicate sits in a review queue until someone clears it, while a false
merge destroys two records and nobody finds out.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from backend.app.llm.provider import LLMProvider
from backend.app.pipeline.records import NormalizedRecord

# ---------------------------------------------------------------------------
# Every tunable number in the pipeline, in one place.
# ---------------------------------------------------------------------------

CONFIG = {
    # Stage 2 thresholds.
    "AUTO_MERGE_AT": 92,      # >= this: merge, but still logged for review
    "ESCALATE_FLOOR": 78,     # >= this and below AUTO_MERGE_AT: ask the LLM
                              # below ESCALATE_FLOOR: drop, never seen again

    # Stage 2 scoring weights. Name dominates; company corroborates.
    "NAME_WEIGHT": 0.65,
    "COMPANY_WEIGHT": 0.35,
    "TITLE_BONUS": 3,         # weak tiebreaker: same canonical role
    "TITLE_PENALTY": 2,       # weak tiebreaker: different canonical role

    # Two records at one company, with near-identical names and DIFFERENT roles,
    # are two colleagues. Measured on this dataset that is the only signal that
    # separates the highest-scoring near miss (92.09, roles differ) from the
    # lowest-scoring true duplicate (91.0, roles agree) -- no cutoff on the
    # blended score can. So a role disagreement vetoes auto-merge outright
    # rather than deducting two points and losing.
    #
    # The veto only ever moves a pair from auto-merge to escalation. It cannot
    # push one below ESCALATE_FLOOR: buying a second look is the point, and
    # turning that into a silent rejection would trade one failure for another.
    # It requires BOTH titles present -- "missing" is not "different", and
    # vetoing on absence would send every incomplete record to the queue.
    "TITLE_DISAGREEMENT_VETOES_AUTO_MERGE": True,

    # A name match with nothing to corroborate it cannot auto-merge. Two real
    # people share a name often enough that 100 on name alone is not evidence.
    "NAME_ONLY_CAP": 91,

    # Company agreement corroborates a name match; it cannot substitute for one.
    # Without this floor, "same employer + same title" drags any vaguely similar
    # name over the auto-merge line -- which is how Michael and Mike get merged
    # without anyone looking, and how two colleagues eventually do too.
    "AUTO_MERGE_MIN_NAME": 90,

    # Initial-aware name matching: "P. Raghavan" vs "Priya Raghavan".
    "INITIAL_MATCH_SCORE": 95,

    # Stage 0 safety valve. A blocking bucket larger than this is a bad key
    # (a whole city, an empty company) and gets skipped rather than exploding
    # into thousands of pairs.
    "MAX_BUCKET_SIZE": 40,
    "LAST_NAME_PREFIX": 3,

    # The fifth key exists because held-out draws exposed a gap the tuned draw
    # did not: every other key needs a SECOND field to survive. `Joseph
    # Whitfield` and `JOSEPH W.` at one company produced co:...|whitfield and
    # co:...|w, which differ precisely because the surname was abbreviated --
    # the exact noise stage 2 was built to absorb, invisible to the stage that
    # decides whether stage 2 ever sees the pair. A key on the first name alone
    # needs nothing else present, and MAX_BUCKET_SIZE catches any name common
    # enough to matter.
    "FIRST_NAME_KEY": True,

    # Stage 3.
    "LLM_BATCH_SIZE": 10,
}

Verdict = Literal["same_person", "different_people", "insufficient_evidence"]

# What happens to the pair in the operator's queue.
#   auto_merged -> merged already, shown so a human can undo it
#   pending     -> needs a decision before anything happens
#   rejected    -> the pipeline decided these are different people
ReviewState = Literal["auto_merged", "pending", "rejected"]


@dataclass
class Pair:
    a_id: str
    b_id: str
    stage: str                     # deterministic | fuzzy | llm
    method: str
    verdict: Verdict
    review_state: ReviewState
    score: float | None = None
    confidence: float | None = None
    reason: str = ""
    llm_used: bool = False
    blocking_keys: tuple[str, ...] = ()

    def key(self) -> tuple[str, str]:
        return (self.a_id, self.b_id)


@dataclass
class DedupeResult:
    pairs: list[Pair] = field(default_factory=list)
    funnel: dict[str, int] = field(default_factory=dict)
    blocking_keys: dict[str, int] = field(default_factory=dict)

    def by_state(self, state: ReviewState) -> list[Pair]:
        return [p for p in self.pairs if p.review_state == state]

    def merged_pairs(self) -> list[Pair]:
        return [p for p in self.pairs if p.verdict == "same_person"
                and p.review_state == "auto_merged"]


# ---------------------------------------------------------------------------
# Stage 0 -- blocking
# ---------------------------------------------------------------------------


def blocking_keys(record: NormalizedRecord) -> list[str]:
    """The keys under which this record is willing to be compared.

    Four keys, each catching a different failure of the others: LinkedIn and
    email catch renames, company+surname catches address changes, and the
    surname-prefix+city key catches records that have lost their identifiers
    entirely.
    """
    keys: list[str] = []
    if record.linkedin_slug:
        keys.append(f"li:{record.linkedin_slug}")
    if record.email_local:
        keys.append(f"em:{record.email_local}")
    if record.company_key and record.last_name:
        keys.append(f"co:{record.company_key}|{record.last_name}")
    if record.last_name and record.city:
        prefix = record.last_name[: CONFIG["LAST_NAME_PREFIX"]]
        keys.append(f"nl:{prefix}|{record.city}")
    if CONFIG["FIRST_NAME_KEY"] and record.first_name:
        keys.append(f"nm:{record.first_name}")
    return keys


def build_candidates(records: Sequence[NormalizedRecord]) -> tuple[
        dict[tuple[str, str], set[str]], dict[str, int], int]:
    """Return (candidate pairs -> keys that produced them, key-type counts, skipped buckets)."""
    buckets: dict[str, list[str]] = {}
    for record in records:
        for key in blocking_keys(record):
            buckets.setdefault(key, []).append(record.id)

    candidates: dict[tuple[str, str], set[str]] = {}
    key_counts: dict[str, int] = {}
    skipped = 0

    for key, ids in buckets.items():
        if len(ids) < 2:
            continue
        if len(ids) > CONFIG["MAX_BUCKET_SIZE"]:
            skipped += 1
            continue
        key_type = key.split(":", 1)[0]
        for a, b in itertools.combinations(sorted(ids), 2):
            candidates.setdefault((a, b), set()).add(key_type)
            key_counts[key_type] = key_counts.get(key_type, 0) + 1

    return candidates, key_counts, skipped


# ---------------------------------------------------------------------------
# Stage 1 -- deterministic
# ---------------------------------------------------------------------------


def deterministic_verdict(a: NormalizedRecord, b: NormalizedRecord) -> str | None:
    """Identical email or identical LinkedIn slug. Not a judgment call."""
    if a.email_normalized and a.email_normalized == b.email_normalized:
        return "exact_email"
    if a.linkedin_slug and a.linkedin_slug == b.linkedin_slug:
        return "exact_linkedin"
    return None


# ---------------------------------------------------------------------------
# Stage 2 -- fuzzy
# ---------------------------------------------------------------------------


def _initials_compatible(short: str, long: str) -> bool:
    return len(short) == 1 and long.startswith(short)


def name_similarity(a: NormalizedRecord, b: NormalizedRecord) -> float:
    """token_set_ratio, lifted when the difference is only an abbreviation.

    `Ines B.` and `Ines Bakshi` score badly on raw token overlap but are the
    same shape of evidence as a full match, so an initial that is consistent
    with the other record's surname scores as a near match instead.
    """
    if not (a.name and b.name):
        return 0.0
    base = float(fuzz.token_set_ratio(a.name_normalized, b.name_normalized))

    a_first, a_last = a.first_name, a.last_name
    b_first, b_last = b.first_name, b.last_name
    if a_first and b_first and a_last and b_last:
        first_ok = (a_first == b_first
                    or _initials_compatible(a_first, b_first)
                    or _initials_compatible(b_first, a_first))
        last_ok = (a_last == b_last
                   or _initials_compatible(a_last, b_last)
                   or _initials_compatible(b_last, a_last))
        if first_ok and last_ok and not (a_first == b_first and a_last == b_last):
            base = max(base, float(CONFIG["INITIAL_MATCH_SCORE"]))
    return base


def company_similarity(a: NormalizedRecord, b: NormalizedRecord) -> float | None:
    """None means "cannot compare", which is different from "does not match"."""
    if not (a.company_key and b.company_key):
        return None
    return float(fuzz.token_sort_ratio(a.company_key, b.company_key))


def fuzzy_score(a: NormalizedRecord, b: NormalizedRecord) -> tuple[float, dict]:
    """Blend name and company, nudge by title agreement, clamp to 0..100."""
    name = name_similarity(a, b)
    company = company_similarity(a, b)

    if company is None:
        # Nothing corroborates the name, so the pair is capped below the
        # auto-merge line and can only ever escalate. This is the single most
        # important line in the file for precision.
        score = min(name, float(CONFIG["NAME_ONLY_CAP"]))
        corroborated = False
    else:
        score = CONFIG["NAME_WEIGHT"] * name + CONFIG["COMPANY_WEIGHT"] * company
        corroborated = True

    # title_agrees stays None when either title is missing. That is deliberate:
    # absence is not disagreement, and the veto below must not fire on it.
    title_agrees = None
    if a.title_canonical and b.title_canonical:
        title_agrees = a.title_canonical == b.title_canonical
        score += CONFIG["TITLE_BONUS"] if title_agrees else -CONFIG["TITLE_PENALTY"]

    # Three independent reasons a pair may not auto-merge, all expressed as the
    # same cap so that `score >= AUTO_MERGE_AT` stays the single merge test.
    if not corroborated or name < CONFIG["AUTO_MERGE_MIN_NAME"]:
        score = min(score, float(CONFIG["NAME_ONLY_CAP"]))

    title_vetoed = False
    if (CONFIG["TITLE_DISAGREEMENT_VETOES_AUTO_MERGE"]
            and title_agrees is False
            and score >= CONFIG["AUTO_MERGE_AT"]):
        # Only ever applied to a pair that would otherwise have merged, and
        # floored at ESCALATE_FLOOR so the veto can never become a rejection.
        score = max(float(CONFIG["ESCALATE_FLOOR"]),
                    min(score, float(CONFIG["NAME_ONLY_CAP"])))
        title_vetoed = True

    score = max(0.0, min(100.0, score))

    return round(score, 2), {
        "name": round(name, 2),
        "name_strong": name >= CONFIG["AUTO_MERGE_MIN_NAME"],
        "company": None if company is None else round(company, 2),
        "title_agrees": title_agrees,
        "title_vetoed": title_vetoed,
        "corroborated": corroborated,
    }


# ---------------------------------------------------------------------------
# Stage 3 -- LLM adjudication
# ---------------------------------------------------------------------------

ADJUDICATION_PROMPT = """\
You are adjudicating possible duplicate records in a private professional network.

Decide only from the fields provided. Never infer facts that are not present.
Two people at the same company with similar names are DIFFERENT people unless an
identifier (email, linkedin) or a distinctive biographical detail links them.
A false merge is unrecoverable; a missed duplicate is not. When evidence is thin,
return insufficient_evidence.

For each numbered pair below, return one verdict object.

verdict must be exactly one of:
  same_person            an identifier or a distinctive detail links the two records
  different_people       the records contradict each other, or are merely similar
  insufficient_evidence  you cannot tell from what is here

confidence is 0.0 to 1.0.
reason is ONE sentence and must name the specific fields you used.

Respond with JSON only, in this shape:
{schema}

PAIRS:
{pairs}
"""


class PairVerdict(BaseModel):
    pair_index: int
    verdict: Literal["same_person", "different_people", "insufficient_evidence"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class BatchVerdicts(BaseModel):
    verdicts: list[PairVerdict]


def _describe(record: NormalizedRecord) -> str:
    fields = [
        ("name", record.raw_name),
        ("email", record.raw_email),
        ("linkedin", record.linkedin_slug),
        ("company", record.raw_company),
        ("title", record.raw_title),
        ("location", record.raw_location),
        ("bio", record.bio),
    ]
    return "\n".join(f"      {label}: {value if value else '(missing)'}"
                     for label, value in fields)


def render_pair_block(index: int, a: NormalizedRecord, b: NormalizedRecord,
                      score: float) -> str:
    return (f"  Pair {index} (fuzzy score {score}):\n"
            f"    Record A ({a.id}):\n{_describe(a)}\n"
            f"    Record B ({b.id}):\n{_describe(b)}")


def adjudicate_batch(provider: LLMProvider, batch: list[tuple[NormalizedRecord,
                                                              NormalizedRecord, float]]
                     ) -> dict[int, PairVerdict]:
    """One LLM call for up to LLM_BATCH_SIZE pairs. Returns verdicts by index."""
    from backend.app.llm.provider import schema_hint

    pairs_text = "\n\n".join(
        render_pair_block(i, a, b, score) for i, (a, b, score) in enumerate(batch))
    prompt = ADJUDICATION_PROMPT.format(
        schema=schema_hint(BatchVerdicts), pairs=pairs_text)

    response = provider.complete_json("dedupe_adjudication", prompt, BatchVerdicts)
    return {v["pair_index"]: PairVerdict(**v) for v in response["verdicts"]}


def _batched(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(records: Sequence[NormalizedRecord],
        provider: LLMProvider | None = None) -> DedupeResult:
    """Run all four stages. `provider=None` runs stages 0-2 and leaves the
    escalated pairs pending, which is what an offline run without a key does."""
    by_id = {r.id: r for r in records}
    n = len(records)
    result = DedupeResult()
    funnel = result.funnel

    funnel["records"] = n
    funnel["possible_pairs"] = n * (n - 1) // 2

    # --- stage 0 ---------------------------------------------------------
    candidates, key_counts, skipped = build_candidates(records)
    funnel["candidate_pairs"] = len(candidates)
    funnel["blocking_buckets_skipped"] = skipped
    result.blocking_keys = key_counts

    # --- stage 1 ---------------------------------------------------------
    remaining: list[tuple[str, str, set[str]]] = []
    stage1 = 0
    for (a_id, b_id), keys in sorted(candidates.items()):
        a, b = by_id[a_id], by_id[b_id]
        method = deterministic_verdict(a, b)
        if method:
            stage1 += 1
            result.pairs.append(Pair(
                a_id=a_id, b_id=b_id, stage="deterministic", method=method,
                verdict="same_person", review_state="auto_merged", score=100.0,
                confidence=1.0, blocking_keys=tuple(sorted(keys)),
                reason=("identical normalized email" if method == "exact_email"
                        else "identical linkedin slug"),
            ))
        else:
            remaining.append((a_id, b_id, keys))

    funnel["stage1_merged"] = stage1
    funnel["stage2_scored"] = len(remaining)

    # --- stage 2 ---------------------------------------------------------
    escalations: list[tuple[str, str, float, dict, set[str]]] = []
    auto = dropped = 0
    for a_id, b_id, keys in remaining:
        a, b = by_id[a_id], by_id[b_id]
        score, parts = fuzzy_score(a, b)

        if score >= CONFIG["AUTO_MERGE_AT"]:
            auto += 1
            result.pairs.append(Pair(
                a_id=a_id, b_id=b_id, stage="fuzzy", method="fuzzy_auto",
                verdict="same_person", review_state="auto_merged", score=score,
                confidence=round(score / 100, 3), blocking_keys=tuple(sorted(keys)),
                reason=(f"name {parts['name']}, company {parts['company']}, "
                        f"title agrees: {parts['title_agrees']}"),
            ))
        elif score >= CONFIG["ESCALATE_FLOOR"]:
            escalations.append((a_id, b_id, score, parts, keys))
            if parts.get("title_vetoed"):
                funnel["stage2_title_vetoed"] = funnel.get("stage2_title_vetoed", 0) + 1
        else:
            dropped += 1

    funnel.setdefault("stage2_title_vetoed", 0)
    funnel["stage2_auto_merged"] = auto
    funnel["stage2_escalated"] = len(escalations)
    funnel["stage2_dropped"] = dropped

    # --- stage 3 ---------------------------------------------------------
    funnel["stage3_pairs"] = len(escalations)
    funnel["stage3_batches"] = 0
    funnel["stage3_same_person"] = 0
    funnel["stage3_different_people"] = 0
    funnel["stage3_insufficient_evidence"] = 0
    funnel["stage3_unadjudicated"] = 0

    if not escalations:
        return result

    if provider is None:
        funnel["stage3_unadjudicated"] = len(escalations)
        for a_id, b_id, score, parts, keys in escalations:
            result.pairs.append(Pair(
                a_id=a_id, b_id=b_id, stage="fuzzy", method="escalated_not_adjudicated",
                verdict="insufficient_evidence", review_state="pending", score=score,
                blocking_keys=tuple(sorted(keys)),
                reason="escalated to stage 3 but no LLM provider was configured",
            ))
        return result

    escalations.sort(key=lambda e: (e[0], e[1]))  # determinism: fixed batching
    for batch in _batched(escalations, CONFIG["LLM_BATCH_SIZE"]):
        funnel["stage3_batches"] += 1
        payload = [(by_id[a_id], by_id[b_id], score) for a_id, b_id, score, _p, _k in batch]
        verdicts = adjudicate_batch(provider, payload)

        for index, (a_id, b_id, score, parts, keys) in enumerate(batch):
            verdict = verdicts.get(index)
            if verdict is None:
                # The model skipped a pair. That is not a licence to guess.
                funnel["stage3_insufficient_evidence"] += 1
                result.pairs.append(Pair(
                    a_id=a_id, b_id=b_id, stage="llm", method="llm_adjudication",
                    verdict="insufficient_evidence", review_state="pending",
                    score=score, llm_used=True, blocking_keys=tuple(sorted(keys)),
                    reason="no verdict returned for this pair",
                ))
                continue

            funnel[f"stage3_{verdict.verdict}"] += 1
            # insufficient_evidence never auto-merges. It goes to a human.
            state: ReviewState = (
                "auto_merged" if verdict.verdict == "same_person"
                else "rejected" if verdict.verdict == "different_people"
                else "pending"
            )
            result.pairs.append(Pair(
                a_id=a_id, b_id=b_id, stage="llm", method="llm_adjudication",
                verdict=verdict.verdict, review_state=state, score=score,
                confidence=verdict.confidence, reason=verdict.reason,
                llm_used=True, blocking_keys=tuple(sorted(keys)),
            ))

    return result
