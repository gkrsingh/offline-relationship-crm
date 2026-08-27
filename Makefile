.PHONY: data db pipeline evaluate holdout test all clean

PY ?= python

## regenerate the shipped synthetic network (seed 42)
data:
	$(PY) backend/scripts/generate_data.py

## rebuild data/crm.db from the committed JSON
db:
	$(PY) backend/scripts/init_db.py --reset

## normalise + dedupe, printing the funnel
pipeline:
	$(PY) backend/scripts/run_pipeline.py

## precision / recall against ground truth (draw A -- the tuned draw)
evaluate:
	$(PY) backend/scripts/evaluate.py

## held-out evaluation: a dataset the thresholds have never seen.
## SEED=<n> to pick a different draw.
SEED ?= 2027
holdout:
	$(PY) backend/scripts/holdout.py --seed $(SEED) --out data/holdout_$(SEED)

## every unseen draw used in NOTE.md
holdout-all:
	@for s in 2027 2028 777 31337 99; do \
		$(PY) backend/scripts/holdout.py --seed $$s --no-llm --out data/holdout_$$s --json; \
	done

## AI enrichment over the canonical people (resumable, cached per person)
enrich:
	$(PY) backend/scripts/enrich.py --batch-size 4 --pace 14 --resume

## rebuild the enrichment table from cached responses, no network
recover-enrichment:
	$(PY) backend/scripts/recover_enrichment.py

## deterministic applicant scoring + the reviewer note written from it
score:
	$(PY) backend/scripts/score_applicants.py --pace 12

## introduction suggestions: embeddings, filters, then copy
intros:
	$(PY) backend/scripts/suggest_intros.py --pace 14 --resume

## what the intro copy would cost, without spending it
intros-estimate:
	$(PY) backend/scripts/suggest_intros.py --estimate

## backfill everything the model owes, most-visible-first
backfill:
	$(PY) backend/scripts/backfill.py

## build the frontend and serve the whole app on :8000
ui:
	cd frontend && npm install && npm run build
	$(PY) -m uvicorn backend.app.api.main:app --port 8000

test:
	$(PY) -m pytest

all: data db pipeline evaluate test

clean:
	rm -rf data/holdout*/ .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
