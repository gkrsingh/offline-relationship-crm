"""The introduction engine. This is the product.

Everything before this stage exists so that this stage has something true to
work with. A clean record is not worth anything on its own; an operator opening
this system wants one question answered — *who should I introduce to whom, and
why* — and the rest is bookkeeping in service of it.

The design has three commitments.

**Complementarity beats similarity.** Two people doing the same thing have a
pleasant conversation. A person who needs what another person offers has a
reason to meet. So `A.needs ↔ B.offers` carries most of the weight and profile
similarity carries a little — enough to break ties between two equally
complementary partners, never enough to manufacture a match on its own.

**An introduction that only helps one side is a favour, not a match.** Both
directions are scored, and a pair where each side has something for the other
earns a reciprocity bonus. A favour is sometimes worth asking for; it should not
outrank a trade.

**The safety filters are deterministic and run before any model sees anything.**
"Do not introduce two people at the same company" must never be a judgment call,
and it must never cost a token. Every rejection records which rule fired, so the
filter set can be argued with from data rather than from intuition.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from backend.app.llm import cache
from backend.app.pipeline.records import NormalizedRecord

CONFIG = {
    # Scoring weights. The first two are complementarity, the third is
    # similarity, and it is deliberately the smallest term in the sum.
    "PRIMARY_DIRECTION_WEIGHT": 0.60,   # the better of A->B and B->A
    "SECOND_DIRECTION_WEIGHT": 0.15,    # the weaker direction
    "SIMILARITY_WEIGHT": 0.10,          # profile <-> profile
    "RECIPROCITY_BONUS": 0.15,          # both directions clear the floor

    # What counts as a real need on the weaker side. Below this the pair is
    # one-directional and gets no bonus.
    "RECIPROCITY_FLOOR": 0.62,

    # bge-small puts unrelated professional text around 0.5, so a floor below
    # that would admit everything. Calibrated against measured pairs, not taste.
    "MIN_DIRECTION": 0.68,
    "MIN_SCORE": 0.55,

    "TOP_N_PER_PERSON": 3,

    # A record too thin to act on should not be offered to anyone.
    "COMPLETENESS_FLOOR": 0.50,

    "EMBED_MODEL": "BAAI/bge-small-en-v1.5",
    "LLM_BATCH_SIZE": 4,
}

# Stages close enough that two founders in one sector are chasing the same
# customers, the same hires and often the same round.
ADJACENT_STAGES = (
    {"pre_seed", "seed"},
    {"seed", "series_a"},
    {"series_a", "series_b"},
    {"series_b", "growth"},
)

FILTERS = ("same_company", "competitors", "incomplete_profile",
           "blocked_pair", "already_introduced", "no_shared_signal")


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def profile_text(record: NormalizedRecord, enrichment: dict | None = None) -> str:
    """What a person is, for similarity. Not what they want."""
    bits = [record.raw_title, record.raw_company, record.bio]
    if enrichment:
        bits += [enrichment.get("persona"), enrichment.get("sector"),
                 enrichment.get("geography")]
    return " ".join(b.replace("_", " ") for b in bits if b) or (record.raw_name or "")


@dataclass
class PersonVectors:
    person_id: str
    needs: list[str]
    offers: list[str]
    need_vectors: np.ndarray      # (n_needs, dim), L2-normalised
    offer_vectors: np.ndarray     # (n_offers, dim)
    profile_vector: np.ndarray    # (dim,)

    @property
    def has_signal(self) -> bool:
        return bool(len(self.needs) or len(self.offers))


def normalise(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.clip(norms, 1e-9, None)


def embed_people(records: Sequence[NormalizedRecord], embedder,
                 enrichment: dict[str, dict] | None = None) -> dict[str, PersonVectors]:
    """Embed every need and offer SEPARATELY, plus one profile vector each.

    Embedding the needs as one blob would give a single similarity number and no
    way to say *which* need matched — and "why they should meet" is the entire
    product. Per-phrase vectors cost nothing extra locally and yield the
    specific pair of phrases that justified the introduction.
    """
    enrichment = enrichment or {}
    texts: list[str] = []
    layout: list[tuple[str, str, int]] = []   # (person_id, kind, index)

    for record in records:
        needs = [n for n in record.needs if n.strip()]
        offers = [o for o in record.offers if o.strip()]
        for i, need in enumerate(needs):
            texts.append(need)
            layout.append((record.id, "need", i))
        for i, offer in enumerate(offers):
            texts.append(offer)
            layout.append((record.id, "offer", i))
        texts.append(profile_text(record, enrichment.get(record.id)))
        layout.append((record.id, "profile", 0))

    vectors = normalise(np.asarray(list(embedder.embed(texts)), dtype=np.float32))

    buckets: dict[str, dict[str, list]] = {
        r.id: {"need": [], "offer": [], "profile": []} for r in records}
    for (person_id, kind, _i), vector in zip(layout, vectors):
        buckets[person_id][kind].append(vector)

    dim = vectors.shape[1] if vectors.size else 384
    out: dict[str, PersonVectors] = {}
    for record in records:
        bucket = buckets[record.id]
        out[record.id] = PersonVectors(
            person_id=record.id,
            needs=[n for n in record.needs if n.strip()],
            offers=[o for o in record.offers if o.strip()],
            need_vectors=(np.vstack(bucket["need"]) if bucket["need"]
                          else np.empty((0, dim), dtype=np.float32)),
            offer_vectors=(np.vstack(bucket["offer"]) if bucket["offer"]
                           else np.empty((0, dim), dtype=np.float32)),
            profile_vector=(bucket["profile"][0] if bucket["profile"]
                            else np.zeros(dim, dtype=np.float32)),
        )
    return out


def best_match(needs: np.ndarray, offers: np.ndarray) -> tuple[float, int, int]:
    """Strongest single need-to-offer pairing, with its indices."""
    if needs.size == 0 or offers.size == 0:
        return 0.0, -1, -1
    sim = needs @ offers.T
    flat = int(np.argmax(sim))
    row, col = divmod(flat, sim.shape[1])
    return float(sim[row, col]), row, col


# ---------------------------------------------------------------------------
# Safety filters -- deterministic, before any model call
# ---------------------------------------------------------------------------


def same_company(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return bool(a.company_key and a.company_key == b.company_key)


def are_competitors(a_enrich: dict | None, b_enrich: dict | None) -> bool:
    """Two founders, one sector, neighbouring stages.

    Deliberately narrow. "Competitor" is not a judgment the system should make
    loosely -- two founders in fintech at wildly different stages are far more
    useful to each other than they are threatened by each other.
    """
    if not (a_enrich and b_enrich):
        return False
    if a_enrich.get("persona") != "founder" or b_enrich.get("persona") != "founder":
        return False
    sector_a, sector_b = a_enrich.get("sector"), b_enrich.get("sector")
    if not sector_a or sector_a in ("unknown", "other") or sector_a != sector_b:
        return False
    stage_a, stage_b = a_enrich.get("company_stage"), b_enrich.get("company_stage")
    if not stage_a or not stage_b or "unknown" in (stage_a, stage_b):
        return False
    return stage_a == stage_b or {stage_a, stage_b} in ADJACENT_STAGES


def actionable(record: NormalizedRecord, vectors: PersonVectors) -> bool:
    if record.completeness.blocked:
        return False
    if record.completeness.score < CONFIG["COMPLETENESS_FLOOR"]:
        return False
    return vectors.has_signal


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------


@dataclass
class Suggestion:
    a_id: str
    b_id: str
    score: float
    complementarity: float        # the stronger direction
    reverse_complementarity: float
    similarity: float
    reciprocal: bool
    matched_need: str             # A's need
    matched_offer: str            # B's offer that answers it
    reverse_matched_need: str = ""
    reverse_matched_offer: str = ""

    # Written later, by the model, for the pairs that survive.
    why: str = ""
    a_gets: str = ""
    b_gets: str = ""
    draft_message: str = ""

    def key(self) -> tuple[str, str]:
        return (min(self.a_id, self.b_id), max(self.a_id, self.b_id))


@dataclass
class IntroResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    funnel: dict[str, int] = field(default_factory=dict)


def score_pair(a: PersonVectors, b: PersonVectors) -> Suggestion | None:
    """Bidirectional. Returns None when neither direction clears the floor."""
    forward, fi, fj = best_match(a.need_vectors, b.offer_vectors)
    backward, bi, bj = best_match(b.need_vectors, a.offer_vectors)

    primary, secondary = max(forward, backward), min(forward, backward)
    if primary < CONFIG["MIN_DIRECTION"]:
        return None

    similarity = float(a.profile_vector @ b.profile_vector)
    reciprocal = secondary >= CONFIG["RECIPROCITY_FLOOR"]

    score = (CONFIG["PRIMARY_DIRECTION_WEIGHT"] * primary
             + CONFIG["SECOND_DIRECTION_WEIGHT"] * secondary
             + CONFIG["SIMILARITY_WEIGHT"] * similarity
             + (CONFIG["RECIPROCITY_BONUS"] if reciprocal else 0.0))

    # Orient the pair so A is the one whose need is answered most strongly.
    if forward >= backward:
        need_owner, offer_owner = a, b
        need_i, offer_j = fi, fj
        rev_i, rev_j = bi, bj
        rev_need_owner, rev_offer_owner = b, a
    else:
        need_owner, offer_owner = b, a
        need_i, offer_j = bi, bj
        rev_i, rev_j = fi, fj
        rev_need_owner, rev_offer_owner = a, b

    return Suggestion(
        a_id=need_owner.person_id,
        b_id=offer_owner.person_id,
        score=round(score, 4),
        complementarity=round(primary, 4),
        reverse_complementarity=round(secondary, 4),
        similarity=round(similarity, 4),
        reciprocal=reciprocal,
        matched_need=need_owner.needs[need_i] if need_i >= 0 else "",
        matched_offer=offer_owner.offers[offer_j] if offer_j >= 0 else "",
        reverse_matched_need=(rev_need_owner.needs[rev_i] if rev_i >= 0 else ""),
        reverse_matched_offer=(rev_offer_owner.offers[rev_j] if rev_j >= 0 else ""),
    )


def build_suggestions(records: Sequence[NormalizedRecord],
                      vectors: dict[str, PersonVectors],
                      enrichment: dict[str, dict] | None = None,
                      blocked: Iterable[tuple[str, str]] = (),
                      introduced: Iterable[tuple[str, str]] = ()) -> IntroResult:
    """Score every eligible pair, filter, then keep the top N per person."""
    enrichment = enrichment or {}
    blocked = {(min(a, b), max(a, b)) for a, b in blocked}
    introduced = {(min(a, b), max(a, b)) for a, b in introduced}

    result = IntroResult()
    rejected = {name: 0 for name in FILTERS}
    by_id = {r.id: r for r in records}

    eligible = [r for r in records if actionable(r, vectors[r.id])]
    result.funnel["people"] = len(records)
    result.funnel["actionable_people"] = len(eligible)
    result.funnel["people_filtered_out"] = len(records) - len(eligible)
    rejected["incomplete_profile"] += len(records) - len(eligible)

    scored: list[Suggestion] = []
    pairs_considered = 0

    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            pairs_considered += 1
            key = (min(a.id, b.id), max(a.id, b.id))

            if same_company(a, b):
                rejected["same_company"] += 1
                continue
            if are_competitors(enrichment.get(a.id), enrichment.get(b.id)):
                rejected["competitors"] += 1
                continue
            if key in blocked:
                rejected["blocked_pair"] += 1
                continue
            if key in introduced:
                rejected["already_introduced"] += 1
                continue

            suggestion = score_pair(vectors[a.id], vectors[b.id])
            if suggestion is None or suggestion.score < CONFIG["MIN_SCORE"]:
                rejected["no_shared_signal"] += 1
                continue
            scored.append(suggestion)

    result.funnel["pairs_considered"] = pairs_considered
    result.funnel["pairs_scored"] = len(scored)

    # Top N per person, as a hard cap on BOTH sides.
    #
    # The obvious implementation -- keep a pair if it is in either person's top
    # three -- lets one popular person appear in a dozen suggestions, because
    # every one of their would-be partners ranks them first. The operator then
    # opens a queue that is mostly one name. So a pair is accepted only while
    # both people still have room, taking the highest-scoring pairs first.
    capacity = {r.id: CONFIG["TOP_N_PER_PERSON"] for r in records}
    kept: dict[tuple[str, str], Suggestion] = {}
    for suggestion in sorted(scored, key=lambda s: (-s.score, s.a_id, s.b_id)):
        if capacity.get(suggestion.a_id, 0) <= 0 or capacity.get(suggestion.b_id, 0) <= 0:
            continue
        kept[suggestion.key()] = suggestion
        capacity[suggestion.a_id] -= 1
        capacity[suggestion.b_id] -= 1

    result.suggestions = sorted(kept.values(), key=lambda s: (-s.score, s.a_id, s.b_id))
    result.rejected = rejected
    result.funnel["suggestions"] = len(result.suggestions)
    result.funnel["reciprocal"] = sum(1 for s in result.suggestions if s.reciprocal)
    result.funnel["people_with_a_suggestion"] = len(
        {i for s in result.suggestions for i in (s.a_id, s.b_id)})
    _ = by_id
    return result


# ---------------------------------------------------------------------------
# The copy. Written by the model, from the matched phrases.
# ---------------------------------------------------------------------------

INTRO_PROMPT = """\
You are drafting introductions inside a private professional network. An
operator will read each one and decide whether to send it. Nothing you write is
sent automatically.

For each numbered pair, write:
  why           one sentence on why these two should meet
  a_gets        what person A gets, one short clause
  b_gets        what person B gets, one short clause
  draft_message a note the operator could send, UNDER 120 WORDS

RULES
- Use only what is given. Do not invent a company, a metric, a mutual friend or
  a shared history.
- Lead with the matched need and offer. That pairing is the reason for the
  introduction; everything else is decoration.
- Where the pair is marked reciprocal, say what each side gives. Where it is
  not, be honest that one side is doing the other a favour -- an operator needs
  to know which ask they are making.
- Address the draft to both people. Warm, brief, no exclamation marks, no
  "I hope this finds you well".

Return JSON:
{schema}

PAIRS:
{pairs}
"""


def render_pair(index: int, suggestion: Suggestion,
                a: NormalizedRecord, b: NormalizedRecord) -> str:
    def describe(record: NormalizedRecord) -> str:
        bits = [record.raw_name or "(unnamed)"]
        if record.raw_title:
            bits.append(record.raw_title)
        if record.raw_company:
            bits.append(record.raw_company)
        return " — ".join(bits)

    lines = [
        f"  pair {index}:",
        f"    person_a: {describe(a)}",
        f"    person_b: {describe(b)}",
        f"    reciprocal: {suggestion.reciprocal}",
        f"    A needs: {suggestion.matched_need}",
        f"    B offers: {suggestion.matched_offer}",
    ]
    if suggestion.reciprocal and suggestion.reverse_matched_need:
        lines += [
            f"    B needs: {suggestion.reverse_matched_need}",
            f"    A offers: {suggestion.reverse_matched_offer}",
        ]
    return "\n".join(lines)


COPY_TASK = "intro_copy_pair"
COPY_VERSION = "intro-v1"


def copy_cache_key(provider, suggestion: "Suggestion") -> str:
    """Key one pair's copy on that pair, not on whoever it was batched with.

    Batch-keying looked fine until the suggestion set shifted by one and every
    key missed at once -- 266 drafted introductions became 93. What the copy
    depends on is this pair and this prompt version, so that is what it is
    keyed on.
    """
    return cache.cache_key(provider.name, provider.model, COPY_TASK, {
        "a": suggestion.a_id, "b": suggestion.b_id,
        "need": suggestion.matched_need, "offer": suggestion.matched_offer,
        "reciprocal": suggestion.reciprocal,
        "version": COPY_VERSION,
    })


def load_cached_copy(provider, suggestion: "Suggestion") -> bool:
    """Fill a suggestion from cache. True if it was there."""
    hit = cache.load(COPY_TASK, copy_cache_key(provider, suggestion))
    if not hit:
        return False
    suggestion.why = hit.get("why", "")
    suggestion.a_gets = hit.get("a_gets", "")
    suggestion.b_gets = hit.get("b_gets", "")
    suggestion.draft_message = hit.get("draft_message", "")
    return bool(suggestion.why)


def store_copy(provider, suggestion: "Suggestion") -> None:
    cache.store(COPY_TASK, copy_cache_key(provider, suggestion),
                provider=provider.name, model=provider.model,
                request={"a": suggestion.a_id, "b": suggestion.b_id},
                response={"why": suggestion.why, "a_gets": suggestion.a_gets,
                          "b_gets": suggestion.b_gets,
                          "draft_message": suggestion.draft_message})


def estimate_tokens(suggestions: Sequence[Suggestion], schema_chars: int,
                    batch_size: int | None = None) -> dict:
    """Estimate the cost BEFORE spending it.

    Rough by design -- four characters to the token is close enough to decide
    whether a run is twenty minutes or four hours, which is the only decision
    this number informs.
    """
    batch_size = batch_size or CONFIG["LLM_BATCH_SIZE"]
    batches = -(-len(suggestions) // batch_size) if suggestions else 0
    prompt_chars = len(INTRO_PROMPT) + schema_chars + batch_size * 420
    prompt_tokens = batches * (prompt_chars // 4)
    completion_tokens = batches * batch_size * 170
    return {
        "suggestions": len(suggestions),
        "batch_size": batch_size,
        "batches": batches,
        "est_prompt_tokens": prompt_tokens,
        "est_completion_tokens": completion_tokens,
        "est_total_tokens": prompt_tokens + completion_tokens,
    }
