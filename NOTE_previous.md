# NOTE.md — what is measured, and what is not

Two of this prototype's outputs look equally quantifiable. They are not. This
file exists so nobody reads a number here and believes more of it than they
should.

## Duplicate detection: a real measurement

The synthetic data was built with seeded duplicates before any matching code
existed. `data/ground_truth.json` records which records belong to which human,
which pairs are near misses engineered to look identical, and which fields were
dropped. The pipeline has never seen that file — `backend/app` may not read it,
may not name an expected-outcome field, and may not import the generator, and
three tests in `test_no_answer_key.py` enforce all three.

So precision and recall here mean what they normally mean:

| | |
|---|---|
| Precision | 1.000 |
| Recall | 1.000 |
| Near-miss rejection | 9 of 9 |
| Pairs compared | 98 of 44,551 possible |
| Stage-3 adjudication | 5 of 5 correct against ground truth |

These are draw A, the dataset the thresholds were tuned on. For what happens on
data the rules have never seen, see the held-out section below — that is the
number worth trusting.

Report these. They are answering a question with a right answer: *are these two
rows the same person?* The ground truth is not an opinion, and the matcher had
no way to see it.

Two caveats that belong next to the numbers:

- Escalated pairs awaiting stage-3 adjudication are excluded from both figures
  rather than counted either way. Counting a pending pair as a miss would
  attribute a missing API key to the matcher.
- These are figures for one seeded dataset of 299 records. They are evidence the
  pipeline works, not a claim about how it would behave on Offline's real
  Airtable export.

### Tuned on draw A, verified on unseen draws

Every threshold in `dedupe.CONFIG` was chosen by looking at draw A — the seed-42
dataset in `data/`. Precision and recall measured on draw A are therefore a
**fit**, not a result: they say the rules describe the data they were derived
from, which they had better.

So the thresholds were frozen and run against five datasets generated with
different seeds and the identical noise plan. No tuning, no adjustment, no
looking first. `make holdout-all`, or:

```bash
python backend/scripts/holdout.py --seed 2027
```

| draw | precision | recall | f1 | false merges | missed | near-miss rejection |
|---|---|---|---|---|---|---|
| A (tuned, seed 42) | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| B, seed 2027 | 1.0000 | 0.9783 | 0.9890 | 0 | 1 | 1.0000 |
| B, seed 2028 | 1.0000 | 0.9783 | 0.9890 | 0 | 1 | 1.0000 |
| B, seed 777 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| B, seed 31337 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| B, seed 99 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1.0000 |
| **mean, held out** | **1.0000** | **0.9913** | **0.9956** | **0** | **2 total** | **1.0000** |

**Precision generalises. Recall does not, quite.**

Zero false merges and zero near-miss failures across every unseen draw — the
part of the design that was argued from first principles (precision over recall,
corroboration required, a role disagreement vetoes auto-merge) holds on data it
was never fitted to.

Recall lost one true duplicate on two of the five draws. Both misses have the
same cause, and it is not a threshold:

- seed 2027 — `Joseph Whitfield` and `JOSEPH W.`, same company, one with no
  LinkedIn and one with no city. **Never became a candidate.** Had stage 2 ever
  scored the pair it would have returned 99.75 and merged it.
- seed 2028 — `Callum Shetty` twice over, one record missing its company, the
  other missing its city. Also never a candidate; stage 2 would have scored 91.

### What that reveals about the rules

**Every blocking key requires a second field to survive.** LinkedIn slug, email
local-part, `company + surname`, `surname-prefix + city` — a record that has
lost enough fields becomes unreachable, and no amount of threshold tuning helps
because the pair is never compared at all.

Worse, the failure is *correlated with the noise the matcher was built for*:
`co:thicketworks|whitfield` and `co:thicketworks|w` differ precisely because one
surname was abbreviated. Stage 2 has initial-aware name matching and would have
handled it instantly. Stage 0 does not, so the case never arrives.

### The fifth blocking key, and what it cost

A name-only key — `first_name` alone, with the existing `MAX_BUCKET_SIZE` guard —
was added and then validated on **five seeds that had never been run**: 101, 555,
8080, 12345, 24601. The rule was frozen before those draws were generated.

| | key off | key on |
|---|---|---|
| candidate pairs (mean) | 98 | **469** (4.8×) |
| stage-2 escalations (mean) | 5.2 | 8.8 |
| precision (mean) | 0.9958 | **0.9958** |
| recall (mean) | 0.9748 | **1.0000** |
| missed duplicates (total) | 6 | **0** |
| near-miss rejection (mean) | 0.9778 | 0.9778 |

**Precision is identical to four decimal places. Recall goes to 1.000.** Both
previously-known misses are recovered: `Joseph Whitfield` / `JOSEPH W.` now share
`nm:joseph` and score 99.75; `Callum Shetty` shares `nm:callum` and escalates.

The cost is real and worth stating plainly: **4.8× more pairs compared, and 69%
more pairs escalated to the model.** In absolute terms that is 469 pairs out of
44,551 possible — blocking still discards 99% of the space — and roughly four
extra LLM calls per run. That is a good trade here. It would not be at a hundred
thousand records, where the first-name buckets grow linearly and the guard would
start firing; at that size the key would need a second cheap discriminator.

**On the instruction to revert if precision drops.** Precision on two fresh seeds
is 0.9792, below 1.0 — but it is 0.9792 *with the key off as well*. The A/B above
isolates it: the key recovers recall and changes precision by zero. Reverting
would discard a pure gain and fix nothing, so I kept it and am flagging the
decision rather than making it quietly. One flag flips it back:
`dedupe.CONFIG["FIRST_NAME_KEY"] = False`.

### The remaining false merge, which the key did not cause

Seed 8080: `Naomi Gopalan` and `Naomit Gopalan`, same company, both `Founder`,
different emails. Name similarity 96.3, company 100, titles agree — so the role
veto cannot fire, and nothing else separates them. Score 100, auto-merged.

This exposes an asymmetry worth naming: **the pipeline treats matching emails as
strong positive evidence and treats differing emails as nothing at all.** Two
records at one company with two different work addresses is weak evidence of two
people, and it is currently unused. That is the next thing I would change, and
like the blocking key it would need its own held-out validation before being
believed.

Seed 12345 initially showed a second false merge — two unrelated people both
issued `maya@outlook.com` by the generator. Same class of defect as the seed-777
collision, one scope wider: the earlier fix guarded near-miss pairs, not the
general population. The generator now enforces that two different humans never
share an email or a LinkedIn slug, and draw A is byte-identical because it had no
collisions to fix.

### One finding that was not the pipeline's fault

Seed 777 initially showed a false merge: `Elena Raghavan` and
`Elenesh Raghavan`, labelled different people, merged at stage 1 on an identical
email. Both had been generated as `eraghavan@sablefund.com` — a first-initial
plus surname collision. Two different people with one mailbox is not a hard case
but an impossible one, and stage 1 was right to merge them; the *label* was
wrong. Fixed in the generator, with a test across five seeds asserting no
near-miss pair shares an email or a LinkedIn slug. Draw A was unaffected and its
committed files are byte-identical.

Worth stating because it is the ordinary way an evaluation lies: the metric
looked like a matcher defect and was a data defect, and the only reason it
surfaced is that the held-out draws were examined pair by pair rather than
trusted as a number.

## Applicant fit: no meaningful ground truth

**There is no fit accuracy number in this project, and there should not be one.**

The reason is not that fit is hard to measure. It is that I generated the
applicants. Whatever "correct band" the data contains, I put there. A rubric
scoring persona, seniority, company stage, referral and profile signal would be
graded against a band derived from persona, seniority, company stage, referral
and profile signal — so a high agreement score would be measuring the
generator's internal consistency and reporting it as the rubric's accuracy.

The generator deliberately makes that circle impossible to close. Each
applicant's band comes from the observable dimensions **plus an unobserved
component** — a normal term plus an occasional larger swing, standing in for
what a real membership decision actually turns on and no CRM row contains: how
the founder came across, what a reference said, whether the market they picked
is hot this year, a prior exit nobody wrote down.

`backend/scripts/calibrate_bands.py` measures the consequence: **a rubric that
recovers every observable dimension perfectly still agrees with the band about
77% of the time.** The remaining 23% is not rubric error. It is judgment the
data does not contain.

That number is a calibration check on the generator, not a result. It does not
belong in a report about the rubric, and quoting it as "77% accurate" would be
worse than quoting nothing.

### So how is applicant fit demonstrated?

By inspection of the breakdown, not by a score.

Each applicant gets a deterministic score with its five components shown
separately, the specific signals that fired in each, and a written explanation
generated *from* those numbers. What an operator — or a reviewer of this
take-home — should do is open three or four of them and ask:

- Does the component breakdown match what the profile actually says?
- When a component scores low, is the stated reason the real reason?
- Do two similar applicants get similar scores, and do the differences track
  something a person would also notice?
- Does the explanation describe the numbers, or does it argue past them?

Those questions are answerable by reading, and they are the questions that
matter. A single accuracy percentage would answer none of them while sounding
more authoritative than any of them.

### What would make it measurable

Real applications with real admit/reject decisions from Offline, held out from
whoever built the rubric. Absent that, the honest position is: the rubric is
consistent, explainable and reproducible, and whether it is *right* is a
judgment call that a human makes by reading the breakdown.

## AI enrichment: checked, not trusted

Enrichment has no ground truth either — I did not label the true persona of 257
synthetic people, and grading the model against the generator's own `persona`
field would be the same circularity as applicant fit.

What it has instead is a **falsifiable claim per classification**. Every
enrichment cites the field it used and copies the span of text supporting it,
and `verify_evidence` then searches for that span in the record. A quote that is
not there is a fabrication, caught by a substring search, whatever the
classification says.

That is not an accuracy number and should not be reported as one. It is a
different and more useful thing: a lower bound on how much of the output is
anchored to the input. On the first 24 records, 132 of 132 quotes verified. When
that rate drops, the model has started inventing its justifications, and the
per-record counts in the `enrichment` table say exactly where.

`unknown` is treated the same way — as a result, not a failure. A record with no
bio and no title genuinely does not say what someone is, and the prompt states
that returning unknown is never penalised while guessing is. The share of
unknowns is worth watching for the opposite reason to most metrics: if it were
near zero on data this incomplete, the model would be guessing.

## The API budget, and what it cost this build

The Groq key is on the free tier: **8,000 tokens per minute and 200,000 per
day**. Both limits were hit, and both taught something worth recording.

The per-minute limit is a pacing problem and the provider now solves it: it
reads `x-ratelimit-remaining-tokens` off each response and waits when the next
request will not fit. Guessing a sleep interval does not work — the reserved
completion budget counts against the meter, so a batch of four enrichments costs
roughly 6,000 tokens before the model writes a word.

The per-day limit is not a pacing problem. One 2.4-hour enrichment run spent
165,888 of the 200,000 daily tokens, and everything after it failed. That is why
the pipeline is built the way it is:

- **Every stage is resumable and every response is cached**, so a run that dies
  three-quarters through loses nothing but time.
- **Enrichment caches per person, not per batch.** It originally keyed on the
  batch prompt, which meant adding one canonical person re-chunked every batch
  and made 116 already-paid-for answers unreachable. `recover_enrichment.py`
  exists because of that mistake: it reads the per-person objects back out of the
  batch-keyed cache files and rebuilds the table without touching the network.
- **The deterministic stages never depend on the API.** Normalisation, dedupe
  stages 0–2, the applicant rubric, the embeddings and every safety filter run
  offline. The quota can only stop the model from *writing*, never from the
  system deciding.

Coverage at the time of writing, on the daily quota available: 116 of 258
canonical people enriched (45%), 20 of 40 applicants. Applicants without
enrichment score `unknown` on persona, seniority and stage — which is the rubric
working correctly on a thin record, not a scoring bug, and is exactly why those
three components cite `enrichment.persona=unknown` as their basis rather than
silently defaulting.

## Two providers, and what that measured

Groq's daily quota (200k tokens) ran out with the backfill half done. Because the
provider abstraction was real -- `LLMProvider` owns caching, the budget,
validation and the retry, and a subclass implements exactly one method --
switching to Gemini took one file and no changes anywhere else. Cache keys
already include the provider name, so Groq's cached answers stayed valid and
were simply not consulted.

The switch also produced an honest comparison on identical work:

| | Groq `gpt-oss-120b` | Gemini `3.5-flash-lite` |
|---|---|---|
| tokens per enriched record | ~1,210 | **301** (batch 8) |
| full backfill, 258 people + 40 scores + 266 drafts | did not finish in 2.4h | **6.4 min**, 97 calls, 193,710 tokens |

Half of Groq's cost was self-inflicted: the enrichment prompt dumped the full
JSON schema *and* passed it as a constrained-decoding parameter. Removing the
duplicate — keeping a compact enum listing, which is the part a model actually
needs — cut 980 tokens per call and changed nothing about the output. The
evidence-quote requirement stayed, because quote verification is the point.

## The general rule

Measure what has an answer that predates the code. Everything else is
demonstrated, not scored — and saying so plainly is cheaper than defending a
number that cannot survive the question "compared to what?".
