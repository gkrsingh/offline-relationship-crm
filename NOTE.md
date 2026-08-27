# NOTE.md — draft

*Working notes, written to be rewritten in your voice. Every number is pulled
from the repo as it stands.*

---

## 1. What I built

An AI-native relationship layer over a private network of 299 messy records —
founders, operators, investors, senior ICs and service providers.

Not a CRM. It answers three questions an operator actually asks:

1. **Which records can I trust?** — duplicates resolved, incompleteness measured
2. **Who should I introduce to whom, and why?** — needs matched against offers
3. **Which applicants deserve a conversation?** — a fixed rubric, explained

It runs end to end: synthetic data → normalisation → deduplication → AI
enrichment → applicant scoring → introduction engine → operator UI. **339 tests.**
The deployed demo needs no API key.

The landing page is a work queue, not a dashboard. Every merge is reversible,
every introduction needs approval, nothing sends itself.

---

## 2. Architecture

```
data/raw/people_raw.json      299 messy records, seeded noise
        ↓
  normalisation               deterministic: emails, LinkedIn slugs, company
        ↓                     suffixes, title aliases, completeness
  stage 0  blocking           44,551 possible pairs → 455 candidates
  stage 1  exact identifiers  40 merged, zero judgment, zero cost
  stage 2  RapidFuzz          415 scored → 7 merge, 12 escalate, 396 drop
  stage 3  LLM adjudication   12 pairs, 2 calls, may abstain
        ↓
  survivorship                39 merged clusters, 3 held on a field conflict
        ↓
  AI enrichment               257/257, closed enums, every quote verified
        ↓
  applicant scoring           deterministic rubric; LLM writes prose from it
        ↓
  introduction engine         local embeddings, 265 suggestions, all drafted
        ↓
  FastAPI + React             one process, one container, port 7860
```

**The layering rule:** `people` holds records exactly as ingested and is never
rewritten. Every derived stage is an additive table keyed by `person_id`. Any
stage can be recomputed without losing source data, and the UI can always show
the source value beside the derived one.

**The provider abstraction:** `LLMProvider` owns caching, the call budget, JSON
validation, the one validation retry and offline mode. A subclass implements
exactly one method. That seam paid for itself — §3.

### The funnel, draw A

| stage | in | out |
|---|---|---|
| possible pairs | — | 44,551 |
| stage 0 blocking | 44,551 | **455** candidates — 99.0% never compared |
| stage 1 exact identifiers | 455 | 40 merged · **0 LLM calls** |
| stage 2 RapidFuzz | 415 | 7 auto-merged, 12 escalated, 396 dropped · **0 LLM calls** |
| stage 3 adjudication | 12 | 1 same_person, 11 different_people, 0 abstentions · **2 LLM calls** |

**Two LLM calls for the entire dedupe pipeline.** One of the stage-2 auto-merges
was blocked by the title veto and pushed to stage 3 instead.

### Tuned on draw A, verified on unseen seeds

Every threshold was chosen by looking at draw A, so draw A is a **fit**, not a
result. The thresholds were frozen and run against six seeds generated afterwards.

| draw | precision | recall | f1 | false merges | missed | near-miss rejection |
|---|---|---|---|---|---|---|
| **A (tuned, seed 42)** | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| 101 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| 555 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| 8080 | 0.9792 | 1.0000 | 0.9895 | **1** | 0 | 0.8889 |
| 12345 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| 24601 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| 31337 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| **mean, held out** | **0.9965** | **1.0000** | **0.9982** | **1 total** | **0** | **0.9815** |

`make holdout-all` reproduces this.

---

## 3. Where AI was actually useful — and where it was not

| job | approach | why |
|---|---|---|
| normalisation | deterministic | An LLM asked to lowercase an email is slower, costlier, and gives a different answer on Tuesday. |
| exact duplicates | deterministic | Matching emails is not a judgment call. |
| candidate generation | RapidFuzz | Cheap recall over the cross-product. |
| **ambiguous duplicates** | **LLM, may abstain** | Needs world knowledge. 12 pairs of 44,551. |
| **reading bios** | **LLM + verified evidence** | Comprehension over prose no regex survives. |
| completeness | deterministic | It is arithmetic. |
| applicant score | deterministic rubric | Must be identical on re-run and arguable with. |
| **applicant prose** | **LLM, from numbers only** | Writing, not deciding. |
| needs ↔ offers | local embeddings | Semantic, high volume, no API. |
| safety filters | deterministic | "Not colleagues" must never be probabilistic. |
| **intro copy** | **LLM** | Drafting is a writing task. |

### The adjudicator earned its place

Two pairs scored **exactly 91.0** and got **opposite verdicts**:

- `Zoe Kulkarni` @ Granite Pay / `Zoe Kulkarni` @ *(no company)* →
  **same_person**, confidence 0.98 — *"share the exact same title, company,
  location, and nearly identical biographical details"*
- `Rohit Zaidi` / `Rohia Zaidi`, both @ Willow Collective →
  **different_people**, confidence 0.95 — *"the names, emails, linkedins, and
  locations clearly refer to distinct individuals"*

A model ratifying the fuzzy score could not do that. It also rejected the
*highest*-scoring escalation, so it is not following the ordering either.

### The score is computed in code, structurally

`build_explanation_prompt(breakdown, schema_json)` has no parameter through which
a name, company or bio could reach the model. A prompt that says "don't invent
numbers" while handing over the record is a hope; a prompt that only contains the
breakdown is a guarantee. A test passes a breakdown that contradicts the record
and asserts the prose follows the numbers.

**Unsupported numbers in applicant prose: 0 of 40.**

### Evidence verification — the cheapest hallucination detector available

Every classification cites the field it used and copies the supporting span.
`verify_evidence` searches for that span in the record. A quote that is not there
is a fabrication, whatever the classification says.

**833 of 840 quotes verified — 99.2%.** 7 records of 257 carry at least one
unverified quote. Not an accuracy number and it should not be reported as one:
it is a lower bound on how much of the output is anchored to the input.

### Two providers, and what the switch measured

Groq's daily quota (200,000 tokens) ran out with the backfill half done. Because
the abstraction was real, switching to Gemini took one file. Cache keys include
the provider name, so nothing was invalidated and nothing was shared.

| | Groq `gpt-oss-120b` | Gemini `3.5-flash-lite` |
|---|---|---|
| tokens per enriched record | ~1,210 | **301** (batch 8) |
| full backfill¹ | didn't finish in **2.4h** — 165,888 tokens, **45%** | **6 min 21s** — 97 calls, 193,710 tokens, **100%** |

¹ 257 people enriched, 40 scores explained, 265 intro drafts.

Half of Groq's cost was self-inflicted: the enrichment prompt dumped the full
JSON schema (980 tokens, 50% of the prompt) *and* passed it as a
constrained-decoding parameter. Removing the duplicate — keeping a compact enum
listing — cut cost 4× and changed nothing about the output. **The evidence quote
requirement stayed**, because quote verification is the point.

**What ships is a single provider's cache.** 565 files, every one Gemini:

| task | entries |
|---|---|
| `enrichment_person` | 258 |
| `intro_copy_pair` | 265 |
| `applicant_explanation_one` | 40 |
| `dedupe_adjudication` | 2 |

The container pins `LLM_PROVIDER=gemini` to match, because a cache key includes
the provider and one provider cannot read another's answers. §4 covers what that
cost to discover.

### The introduction engine

257 canonical people, 227 actionable, 25,651 pairs considered, 8,465 above the
floor, **265 suggestions** (top 3 per person, capped on both sides), 246
reciprocal, 184 people with a suggestion. Embedding: 20.2s, local CPU.

**Rejected by filter — deterministic, before any model call:**

| filter | count |
|---|---|
| no_shared_signal | 17,120 |
| competitors | 36 |
| incomplete_profile | 30 |
| same_company | 29 |
| blocked_pair | 1 |
| already_introduced | 0 |

Today shows the **10** strongest, with the total stated and a line saying why it
is capped. 265 cards is a backlog; an operator shown everything reads nothing.

### Rule 10: there is no applicant-fit accuracy number

I generated the applicants. Whatever "correct band" the data contains, I put
there. A rubric scoring persona, seniority, stage, referral and profile signal,
graded against a band derived from those same five things, measures the
generator's internal consistency and reports it as the rubric's accuracy.

The generator makes that circle impossible to close: each band comes from the
observable dimensions **plus an unobserved component**, standing in for what a
real membership decision turns on and no CRM row contains. A rubric that recovers
every observable dimension perfectly still agrees with the band about 77% of the
time (`calibrate_bands.py`). **That is a calibration check on the generator, not a
result. Quoting it as "77% accurate" would be worse than quoting nothing.**

Fit is demonstrated by opening three or four breakdowns and reading them. Current
bands: 13 strong, 16 review, 11 weak.

---

## 4. Findings — the bugs worth writing down

**Plain JSON mode truncates silently.** Asked for eight enrichments under Groq's
`json_object` mode, the model returned **five** — well-formed JSON, three records
missing, no error. A batch stage that drops records without failing is the worst
failure mode available. Constrained decoding (`json_schema` on Groq,
`response_schema` on Gemini) fixed it completely, and the schema is now threaded
through `_generate` for exactly this reason.

**A delete-cascade made merges un-reversible.** `duplicate_reviews` cascades off
`duplicate_pairs`, and the pipeline rewrote `duplicate_pairs` on every run — so
recomputation silently destroyed every human decision, and a reverted merge came
straight back. Recomputation may change what the pipeline *thinks*; it may not
forget what a person *decided*. Reviews now survive a rewrite and reverted merge
groups are kept as history.

**The answer key was importable from application code.** `vocab.py` held the
applicant phrase banks keyed by intended band, inside `backend/app`. One import
and a dict lookup recovered the band for **25 of 25** applicants exactly. Moved to
the generator side, with three tests enforcing the fence: no reading ground truth,
no expected-outcome field names, no importing the generator.

**Held-out evaluation found a blocking gap nothing else would have.** Draw A
showed recall 1.000. Two unseen draws lost a duplicate each, with the same cause,
invisible on the tuned draw: *every blocking key required a second field to
survive*. `Joseph Whitfield` and `JOSEPH W.` produced `co:thicketworks|whitfield`
and `co:thicketworks|w`, which differ **precisely because the surname was
abbreviated** — the exact noise stage 2 was built to absorb, invisible to the
stage that decides whether stage 2 ever sees the pair. Stage 2 would have scored
that pair 99.75. A fifth, name-only key fixed it: recall across five fresh seeds
went 0.9748 → 1.0000 with precision unchanged to four decimals. Cost: 4.8× more
candidate pairs.

**One popular person appeared in six suggestions.** "Top 3 per person" as a union
lets a hub exceed the cap, because all six of their would-be partners rank them
first — and the operator opens a queue that is mostly one name. Now greedy, with
capacity enforced on both sides.

**Batch-keyed caching failed three times before I stopped writing it.** The cache
key covered the whole batch prompt, so changing the input set by one re-chunked
every batch and missed every key at once. It cost 116 already-paid-for enrichments
the first time. I fixed enrichment and did not look for the same shape elsewhere —
so it then cost the intro copy, and then the applicant explanations. All three are
now keyed per item: per person, per pair, per applicant. The lesson is not "cache
per item"; it is that finding a bug class and fixing one instance is not fixing
the bug. The same pattern recurred one layer up, in the UI: I swept every list
view for raw enum keys, declared it clean, and never opened a detail panel —
where the applicant explanation bullets had been rendering `persona_fit: 30 of
30` in 76 of 160 bullets the whole time. Two passes missed it because both
looked where the last instance had been.

**`backfill.py` silently rewrote 266 drafted introductions down to 93.** It ran
the intro stage, hit the batch-key problem above, recovered only what happened to
match, and wrote the result over the database — no error, no warning, a number
quietly two-thirds smaller. I found it by reading a coverage line, not by anything
failing. Two changes: an offline cache miss now raises instead of degrading, and
the per-pair keying means a re-run recovers everything. The general point is that
a stage which can partially succeed must say so loudly, because "it ran fine" and
"it destroyed most of the work" looked identical from the outside.

**`recover_enrichment.py` stamped the wrong provider onto Gemini's answers.** It
hardcoded `provider="groq"` when writing recovered rows, so after the switch the
shipped database claimed Groq had classified 257 records that Gemini classified,
and the UI dutifully rendered "Classified by GPT-OSS 120B" under each one.

That is the worst bug in this list, and not because of its size. The entire
argument of this product is that machine-generated data must be visibly marked
and traceable to what produced it — the oxblood rule, the evidence quote on
hover, the model name under every enrichment. A provenance field that is
confidently wrong is worse than one that is absent: it makes the marking a
decoration rather than a claim, and it would have been invisible to anyone who
trusted the interface. Provenance now travels with each cached answer rather than
being supplied by whoever happens to be writing.

**Two generator defects that looked like matcher defects.** Seed 777: two
"different" people both issued `eraghavan@sablefund.com`. Seed 12345, one scope
wider: two unrelated people both issued `maya@outlook.com`. Two people with one
mailbox is not a hard case but an impossible one — **stage 1 was right and the
label was wrong.** The generator now enforces that two different humans never
share an email or LinkedIn slug. Worth recording because it is the ordinary way
an evaluation lies, and it only surfaced because the held-out draws were read
pair by pair rather than trusted as a number.

---

## 5. What I would build next, with another week

1. **Two-way Airtable sync.** Read is the easy half. Writing back means conflict
   resolution against a base people edit by hand — the same survivorship problem
   as merging, one level up.
2. **Relationship decay from real signals.** Every score here is static. Last
   contact, thread depth and meeting history would make "who should I talk to"
   answerable, and would let an intro suggestion age.
3. **Event-triggered automation.** A record changing employer should re-run
   enrichment and re-score their introductions. Today that needs someone to run a
   script.
4. **Closing the loop on intro outcomes.** Approvals are recorded; whether the
   meeting happened, and whether it was useful, is not. Without that the matcher
   can never learn, and every weight in it stays a guess I defended in prose.
5. **An operator-editable rubric.** The weights sit in one CONFIG dict precisely
   so they can move. They should move from a screen, with the change re-scoring
   everyone and showing what it did — which turns the rubric into something
   Offline owns rather than something I chose.
6. **Email disagreement as negative evidence.** The remaining false merge on seed
   8080 — `Naomi Gopalan` / `Naomit Gopalan`, same company, same title, different
   emails — exposes an asymmetry: the pipeline treats matching emails as strong
   positive evidence and differing emails as *nothing at all*. Two records at one
   company with two different work addresses is weak evidence of two people. Like
   the blocking key, it needs its own held-out validation before being believed.

---

## 6. What I would not claim

**The 93% reciprocity rate is a synthetic-data artifact.** 246 of 265 suggestions
are two-way. That is a property of a topic taxonomy I wrote, in which needs and
offers are drawn from the same vocabulary. Real data is far sparser and I would
expect this to fall a long way. The top suggestions cluster on a few topics for
the same reason.

**The competitor filter is barely exercised.** 36 firings out of 25,651 pairs. It
is deliberately narrow — both founders, same sector, adjacent stages — and on this
data it has not been meaningfully tested. It also depends entirely on enrichment:
before coverage reached 100% it fired 6 times.

**The abstain path is untested live.** `insufficient_evidence` is implemented,
never auto-merges, and routes to the queue — but across 12 adjudicated pairs the
model used it **zero times**. The unit tests cover the handling; the behaviour is
unverified. I cannot tell you from evidence that it would abstain when it should.

**The Docker image has never been built.** Docker was not available on the
machine this was written on. Every input is verified — each `COPY` path exists,
requirements are pinned to the versions actually in use, the healthcheck command
exits 0, and the app has been run under the container's exact environment
(`0.0.0.0:7860`, no keys, `LLM_OFFLINE=true`) with all four views exercised. But
`pip install` on `python:3.11-slim` and the FastEmbed weight download are
untested layers, and the first Hugging Face build will be the first real test of
them.

**These numbers describe one seeded dataset of 299 records**, plus six more from
the same generator. They are evidence the pipeline works. They are not a claim
about Offline's real Airtable export.
