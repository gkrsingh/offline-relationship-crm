---
title: Offline — Network Intelligence
emoji: 🍷
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: true
license: mit
short_description: An AI-native relationship layer over a private founder network
---

# Offline — AI-native relationship CRM

A relationship layer over a private network of founders, operators, investors,
senior ICs and service providers.

> This is not a CRM that stores people. It is an AI-native layer that helps
> Offline understand its network and decide what to do next.

The network already exists, in Airtable, half-clean. The value is not in storing
it more neatly — it is in answering three questions an operator actually asks:

1. **Which records can I trust?** — duplicates resolved, incompleteness measured
2. **Who should I introduce to whom, and why?** — needs matched against offers
3. **Which applicants deserve a conversation?** — a fixed rubric, explained

The landing page is a work queue, not a dashboard. Every merge is reversible,
every introduction needs approval, and nothing sends itself.

---

## Run it

```bash
pip install -r requirements.txt
```

```bash
python -m uvicorn backend.app.api.main:app --port 8000
```

Open <http://localhost:8000>. **No API key is needed.** The pipeline has already
been run: `data/crm.db` and `data/cache/llm/` are committed, so every model result
is served from disk.

To rebuild from scratch (this *does* need a key):

```bash
make data db pipeline backfill
```

---

## What is in it

| | |
|---|---|
| Source records | 299 (generated, with seeded noise) |
| Canonical people after dedupe | 257 |
| Duplicate clusters merged | 39 · 3 held on a field conflict |
| AI enrichment coverage | 257 / 257 |
| Evidence quotes verified | 833 / 840 — **99.2%** |
| Membership applicants scored | 40 — 13 strong, 16 review, 11 weak |
| Introduction suggestions | 265, of which 246 reciprocal · all drafted |
| Tests | 332 |

---

## The four screens

**Today** — the queue. Four sections, each row routing into the screen where the
action happens. The headline is what the pipeline resolved *without* a human
(59 pairs into 39 merged records), not what is waiting.

**Duplicates** — two records side by side, matching fields dimmed to near-nothing
and conflicting fields the only thing with contrast on the page. Verdict,
confidence and reason underneath. `M` / `K` / `S`. Split in two: **Needs your
decision** holds only what the pipeline refused to settle; **Recently merged** is
a reversible log with an Undo on every row.

**Introductions** — a card per pair: both people, the matched need and offer, why
they should meet, what each side gets, and a draft under 120 words with a copy
button. Approve / Dismiss / Never suggest this pair.

**People** — filterable table; click for a detail panel with the source record, AI
enrichment with evidence on hover, completeness, the applicant score broken into
its five components, and that person's suggestions.

### Two rules the UI never breaks

**Derived data never looks like source data.** Anything a model produced carries a
1px oxblood rule down its left edge, with the evidence quote on hover. Source data
has no rule. You can tell them apart with the page squinted at.

**Missing enrichment degrades honestly.** A record the backfill has not reached
says *"Not yet enriched — queued for the next backfill pass."* A record the model
*looked at* and could not classify says *"not stated in the record."* Those are
different facts, and the API returns `null` versus `"unknown"` so the UI can tell
them apart. An applicant scored on a thin record says so, in those words.

---

## Architecture

```
data/raw/people_raw.json      299 messy records
        ↓
  normalisation               deterministic: emails, LinkedIn slugs, company
        ↓                     suffixes, title aliases, completeness
  stage 0  blocking           44,551 possible pairs → 455 candidates
  stage 1  exact identifiers  40 merged · 0 LLM calls
  stage 2  RapidFuzz          415 scored → 7 merge, 12 escalate, 396 drop
  stage 3  LLM adjudication   12 pairs · 2 LLM calls · may abstain
        ↓
  survivorship                reversible; conflicts held for a human
        ↓
  AI enrichment               closed enums, `unknown` always valid, quotes verified
        ↓
  applicant scoring           deterministic rubric; the LLM only writes the prose
        ↓
  introduction engine         local embeddings, deterministic safety filters
        ↓
  FastAPI + React             one process, one container, port 7860
```

### The design decision that matters

Every stage is split into *what must be exact* and *what needs judgment*, and
those halves are implemented differently on purpose.

Deduplication is the clearest case. Exact email and LinkedIn matches are not a
judgment call, so they are never sent anywhere. RapidFuzz generates candidates
cheaply. Only the genuinely ambiguous residue reaches a model — **12 pairs out of
44,551** — which returns `same_person | different_people | insufficient_evidence`
with a reason, and is explicitly allowed to abstain.

The applicant score is the same idea from the other side. The rubric is
arithmetic — persona fit 30, seniority 20, company stage 20, referral 15, profile
signal 15 — computed in Python so it is identical on every run. The model receives
the finished breakdown *and nothing else*: no name, no company, no bio. It cannot
introduce a number it was not given, because it never sees one.

Full reasoning, findings and measurement caveats: **[NOTE.md](NOTE.md)**.

---

## Measurement

Tuned on draw A, then frozen and run against six seeds generated afterwards:

| | draw A (tuned) | mean of 6 held-out draws |
|---|---|---|
| precision | 1.0000 | **0.9965** |
| recall | 1.0000 | **1.0000** |
| near-miss rejection | 1.0000 | 0.9815 |
| missed duplicates | 0 | 0 |

`make holdout-all` reproduces it.

**There is no applicant-fit accuracy number**, and there should not be one — I
generated the applicants, so any such figure would measure the generator rather
than the rubric. See NOTE.md §3.

---

## Layout

```
backend/
  app/                the application. Never imports from scripts/.
    api/main.py       FastAPI: the queue, and the actions that empty it
    llm/              provider abstraction, JSON cache, call budget
      provider.py     LLMProvider: cache, budget, validation, retry
      groq_provider.py · gemini_provider.py
    pipeline/
      normalize.py    pure normalization functions
      dedupe.py       stages 0-3, every threshold in one CONFIG
      merge.py        survivorship, reversible
      enrich.py       closed enums + verified evidence
      scoring.py      deterministic rubric; the LLM sees only the breakdown
      intros.py       needs ↔ offers, safety filters, intro copy
      store.py        the only SQL in the pipeline
  scripts/            the generator and operational entry points
    generate_data.py · init_db.py · run_pipeline.py · enrich.py
    score_applicants.py · suggest_intros.py · backfill.py
    evaluate.py       reads ground truth — the only thing allowed to
    holdout.py        same thresholds, a dataset they have never seen
frontend/
  src/                React + TS + Tailwind
  dist/               committed, so the image needs no Node
data/
  crm.db              committed: the demo ships with the pipeline already run
  cache/llm/          committed: every model response, replayable offline
  ground_truth.json   evaluation only
Dockerfile            Hugging Face Spaces, port 7860, no key
```

---

## Deploying

```bash
docker build -t offline-crm .
```

```bash
docker run -p 7860:7860 offline-crm
```

The image contains no key and references none. `frontend/dist` is committed so
there is no Node in the image; `data/crm.db` and `data/cache/` are committed so
the demo is self-contained; the FastEmbed model is baked into a `RUN` layer so no
user's first click triggers a download.
