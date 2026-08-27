# Offline — AI-native relationship CRM (prototype)

A relationship layer over a private network of founders, operators, investors,
senior ICs and service providers.

> This is not a CRM that stores people. It is an AI-native layer that helps
> Offline understand its network and decide what to do next.

The network already exists, in Airtable, half-clean. The value is not in storing
it more neatly — it is in answering three questions an operator actually asks:

1. **Which records can I trust?** — duplicates found, incompleteness measured.
2. **Who should I introduce to whom, and why?** — needs matched against offers.
3. **Which applicants deserve a conversation?** — a fixed rubric, explained.

The landing page is a work queue, not a dashboard. Every merge and every
introduction is a human decision; the system only ever proposes.

## Status

| Phase | | |
|---|---|---|
| 1 | Scaffold, schema, synthetic data | ✅ done |
| 2 | Normalisation + duplicate pipeline | ✅ done, verified on 5 unseen draws |
| 3 | AI enrichment | ✅ done |
| 4 | Applicant scoring | ✅ done |
| 5 | Introduction engine | ✅ done |
| 6 | API | ✅ done |
| 7 | UI | ✅ done |
| 8 | Evaluation | ⬜ |
| 9 | Deployment | ⬜ |

## Architecture

```
 data/raw/people_raw.json          ← messy synthetic export (299 records)
          │
          ▼
   Normalisation                   deterministic: casing, emails, LinkedIn slugs,
          │                        company suffixes, titles, completeness
          ▼
   Duplicate detection             stage 1 exact  → stage 2 RapidFuzz
          │                        → stage 3 LLM adjudication (ambiguous only, may abstain)
          ▼
   Clean canonical people          merges applied only after human review
          │
          ▼
   AI enrichment                   persona, seniority, stage, sector, geography,
          │                        needs, offers — closed enums, `unknown` always
          │                        valid, every quote checked against the record
          ▼
   Applicant scoring               deterministic rubric 40/25/20/15 → LLM writes the prose
          │
          ▼
   Introduction engine             A.needs ↔ B.offers via local embeddings,
          │                        deterministic safety filters, top 3 per person
          ▼
   Operator UI                     Review queue · People · Introductions
```

### The one design decision that matters

Every stage is split into *what must be exact* and *what needs judgment*, and
those halves are implemented differently on purpose.

The applicant score is the clearest example. The rubric is arithmetic —
role relevance 40, AI/automation 25, builder signal 20, founder-office fit 15 —
and it is computed in Python, so it is identical on every run and can be argued
with. The LLM is handed the finished breakdown and asked only to write the
paragraph. It cannot move a number.

Deduplication is the same idea. Exact email and LinkedIn matches are not a
judgment call, so they are not sent anywhere. RapidFuzz generates candidates
cheaply. Only the genuinely ambiguous residue reaches the LLM, which returns
`same_person | different_people | insufficient_evidence` with a reason — and is
explicitly allowed to abstain, because merging two real people is far more
damaging than leaving a duplicate in the queue.

Full table of where AI is and is not used: [CLAUDE.md](CLAUDE.md).

## Data

`data/raw/people_raw.json` is generated, not hand-written, and regenerating it
with the same seed reproduces it exactly.

| | |
|---|---|
| Records | 299 |
| Distinct real people | 254 |
| Duplicate clusters | 42 (3 of them three-way) |
| Near-miss pairs | 9 — different people engineered to look identical |
| Membership applicants | 40 (12 strong / 20 review / 8 weak, bands in ground truth only) |
| Missing email / company / LinkedIn | 30 / 22 / 58 |

Noise is injected deliberately: exact re-imports, work-vs-personal email
variants, nicknames and initials, accent stripping, `Inc.`/`Ltd`/abbreviated
company names, four shapes of LinkedIn URL, title synonyms
(`VP Sales` / `Vice President of Sales`), casing and whitespace damage, and
dropped fields.

The near-miss pairs are the interesting part: two people sharing a surname at one
company, or the same full name at two companies. A matcher tuned for recall
merges them. It should not.

Applicants apply to **join the network**, not for a job: each application says
what they are building now (with a traction number), why they want in, what they
would contribute, and who referred them. Their intended band is computed from a
latent quality model over persona, seniority, company stage, referral and
profile signal — and lives only in ground truth.

`data/ground_truth.json` records which records belong to which human and which
band each applicant should land in. **The application never reads it** — only
`backend/scripts/evaluate.py` does, and three tests enforce the boundary.

## Where the pipeline stands

Measured by `python backend/scripts/evaluate.py`, with stage 3 not yet run so
the escalated pairs are excluded rather than counted either way:

| | |
|---|---|
| Duplicate precision | 1.000 (47 TP, 0 FP) |
| Duplicate recall | 1.000 (0 missed) |
| Near-miss rejection | 9 of 9 |
| Pairs compared | 98 of 44,551 possible |
| LLM calls | 5 pairs, 1 batch |

Applicant fit is deliberately **not** scored for accuracy. See
[NOTE.md](NOTE.md) for why, and for what is measured instead.

## Running it

```bash
pip install -r requirements-dev.txt
```

```bash
python backend/scripts/generate_data.py
```

```bash
python backend/scripts/init_db.py --reset
```

```bash
python -m pytest
```

Then open the app — FastAPI serves the built frontend as one process:

```bash
python -m uvicorn backend.app.api.main:app --port 8000
```

An API key is only needed to *rebuild* the AI outputs. The demo runs from the
committed LLM cache with `LLM_OFFLINE=true` and no key. Copy `.env.example` to
`.env` if you want to regenerate them yourself.

## Layout

```
backend/
  app/                the application. Never imports from scripts/.
    schema.sql        SQLite schema for every phase
    config.py         .env-backed settings
    db.py             connect / apply schema / load records
    llm/              provider abstraction, JSON cache, call budget
      provider.py     LLMProvider base: cache, budget, validation, retry
      groq_provider.py
      gemini_provider.py  stub
      cache.py
    pipeline/
      normalize.py    pure normalization functions
      enrich.py       LLM enrichment: closed enums + verified evidence
      scoring.py      deterministic rubric; the LLM only sees the breakdown
      intros.py       needs<->offers matching, safety filters, intro copy
      completeness.py weighted score + plain-English blocked reason
      records.py      NormalizedRecord
      dedupe.py       stages 0-3, all thresholds in CONFIG
      merge.py        survivorship, reversible
      store.py        the only SQL in the pipeline
  scripts/            the generator and operational entry points
    vocab.py          generator vocabularies -- holds the applicant answer key,
                      which is why it lives here and not under app/
    generate_data.py  synthetic network + ground truth
    init_db.py        build data/crm.db from the JSON
    run_pipeline.py   normalize + dedupe, prints the funnel
    enrich.py         AI enrichment over the canonical people
    evaluate.py       precision/recall vs ground truth (reads the answer key)
    holdout.py        same thresholds, a dataset they have never seen
    calibrate_bands.py generator calibration, not a result
  tests/
data/
  raw/                people_raw.json, applications.json
  cache/llm/          committed LLM responses, one JSON file per key
  ground_truth.json   evaluation only
frontend/             (phase 7)
```
