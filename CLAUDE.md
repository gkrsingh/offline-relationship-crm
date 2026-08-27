# CLAUDE.md — agreed scope

Working agreement for this repository. Read before changing anything.

## What this is

A take-home prototype: an AI-native relationship layer over a private network of
founders, operators, investors, senior ICs and service providers. It is **not** a
CRM. The point is to show judgment about where AI helps and where deterministic
code is the right answer.

The product idea in one line:

> This is not a CRM that stores people. It is an AI-native layer that helps
> Offline understand its network and decide what to do next.

The single most important feature is **useful introduction suggestions**. CRUD is
scaffolding.

## Explicitly out of scope

Do not build any of these, even if they seem like an easy win:

authentication · multi-tenancy · real Airtable integration · email/calendar/
WhatsApp integrations · CRM permissions · mobile optimisation · workflow engines ·
microservices · Kubernetes · Postgres · graph databases · embedding-based
deduplication · analytics dashboards

Everything runs locally as one application and deploys as one Docker image.

## Stack (fixed)

| Layer | Choice |
|---|---|
| Frontend | React + Vite + TypeScript + Tailwind (shadcn/ui if it saves time) |
| Backend | Python + FastAPI |
| Database | SQLite |
| LLM | Groq or Gemini behind one abstraction; `LLM_PROVIDER` switches both |
| Embeddings | FastEmbed, `BAAI/bge-small-en-v1.5`, CPU only |
| Similarity | NumPy cosine |
| Fuzzy matching | RapidFuzz |
| Deploy | Hugging Face Spaces, Docker |

## Rules that are not negotiable

1. **Application code never reads the answer key.** Only `backend/scripts/evaluate.py`
   may read `data/ground_truth.json`. `backend/app` may not read it, may not name
   an expected-outcome field, and may not import the generator — whose
   `latent_score` decides every applicant's band. Three tests in
   `test_no_answer_key.py` enforce all three.
2. **The LLM never computes the applicant score.** Deterministic code produces the
   numbers; the LLM only writes prose *from* those numbers.
3. **No embeddings in deduplication.** Dedupe is exact match → RapidFuzz →
   LLM adjudication on the genuinely ambiguous remainder only.
4. **The LLM may abstain.** `insufficient_evidence` and `persona: unknown` are
   valid answers and must never be coerced into a guess.
5. **Precision over recall on duplicates.** Wrongly merging two real people is
   worse than leaving a duplicate in the queue. Two records at one company with
   near-identical names and different canonical roles are colleagues: a role
   disagreement vetoes auto-merge and sends the pair to stage 3.
10. **Never report an applicant-fit accuracy number.** The bands are generated,
   so any such figure measures the generator. See [NOTE.md](NOTE.md).
6. **Every LLM response is cached** and every classification carries evidence
   naming the input field that supported it.
7. **Every merge and every introduction requires a human decision.** Nothing acts
   on its own.
8. **The deployed demo runs with no API key**, entirely from cached results.
9. **No secrets in the repo.** `.env` is gitignored; `.env.example` is the contract.

## Where AI is used, and where it is not

| Job | Approach | Why |
|---|---|---|
| Normalisation | Deterministic | Rules are exact and auditable. An LLM would be slower, costlier and non-reproducible. |
| Exact duplicates | Deterministic | Matching emails is not a judgment call. |
| Candidate duplicate pairs | RapidFuzz | Cheap recall over the full cross-product. |
| Ambiguous duplicate pairs | LLM, structured JSON, may abstain | Genuinely needs world knowledge (nicknames, company renames). |
| Persona / seniority / sector | LLM with evidence | Reading unstructured bios is what LLMs are for. |
| Completeness scoring | Deterministic | It is arithmetic. |
| Applicant score | Deterministic rubric: persona_fit 30, seniority 20, company_stage 20, referral_signal 15, profile_signal 15; strong >= 75, review 55-74, weak < 55 | Must be consistent, explainable and identical on re-run. |
| Applicant explanation | LLM | Prose written from fixed numbers. |
| Needs ↔ offers matching | Local embeddings + cosine | Semantic, high volume, no API needed. |
| Intro safety filters | Deterministic | Rules like "not colleagues" must never be probabilistic. |
| Intro copy | LLM | Drafting is a writing task. |

## Phases

Build in order. After each phase: run the tests, run the app, verify the feature,
then stop.

1. Scaffold + database + synthetic data ← **done**
2. Normalisation + duplicate pipeline ← **done**
3. AI enrichment ← **done**
4. Applicant scoring ← **done**
5. Introduction engine ← **done**
6. API ← **done**
7. UI ← **done**
8. Evaluation
9. Deployment

## Conventions

- Raw ingested data in `people` is never rewritten. Every derived stage is an
  additive table keyed by `person_id`, so any stage can be recomputed and the UI
  can always show source value beside derived value.
- Pair tables (`duplicate_pairs`, `introductions`, `blocked_pairs`) always store
  `person_a_id < person_b_id` so a pair cannot exist twice under two orderings.
- The dataset is regenerated with `--seed 42`; the committed files in `data/` are
  the demo dataset and a test asserts they match the generator.
- Do not add a dependency unless it materially simplifies the implementation.
- Do not build abstractions for hypothetical requirements.

## Commands

```bash
python backend/scripts/generate_data.py     # regenerate the synthetic network
python backend/scripts/init_db.py --reset   # rebuild data/crm.db from the JSON
python backend/scripts/run_pipeline.py      # normalise + dedupe, prints the funnel
python backend/scripts/run_pipeline.py --no-llm   # stages 0-2 only, no key needed
python backend/scripts/enrich.py            # AI enrichment over canonical people
python backend/scripts/evaluate.py          # precision/recall vs ground truth
python backend/scripts/holdout.py --seed N  # same thresholds, unseen dataset
python backend/scripts/calibrate_bands.py   # generator calibration, NOT a result
python -m pytest                            # run the suite
```

The LLM cache lives in `data/cache/llm/` as one JSON file per key and is
committed. A second pipeline run makes zero API calls.
