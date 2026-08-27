# Deployment image. Runs on Render (where the demo is deployed) and on Hugging
# Face Spaces unchanged -- the only thing that differs between the two is which
# port the host asks for, and that is read from the environment at start.
#
# Four decisions worth stating, because each one is what keeps the demo from
# breaking in front of somebody:
#
# 1. NO API KEY, and none referenced. Every model result is already computed and
#    committed -- in data/crm.db and in data/cache/llm. LLM_OFFLINE=true makes
#    a cache miss fail loudly rather than quietly reaching for a network the
#    container has no credentials for.
#
# 2. NO NODE. frontend/dist is built on a developer machine and committed, so
#    the image has no toolchain, no npm install at build time, and nothing that
#    can fail because a registry is slow.
#
# 3. NO MODEL DOWNLOAD AT REQUEST TIME. FastEmbed is only needed to *recompute*
#    embeddings; the introductions are already in the database. It is installed
#    and its weights are baked into a layer anyway, so that a future rebuild of
#    the intro engine cannot turn a user's first click into a 90 MB download.
#
# 4. THE PORT COMES FROM THE ENVIRONMENT. Render injects $PORT and expects the
#    process to bind it; Spaces routes to 7860. A fixed bind port would mean an
#    image that only works on one of the two.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding weights into the image. If this line is ever removed, the
# first request that touches the intro engine downloads a model instead of
# answering -- which is exactly the failure this RUN exists to prevent.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

COPY backend/ ./backend/
COPY frontend/dist/ ./frontend/dist/
COPY data/ ./data/

# data/crm.db is stored in Git LFS. A host that clones without fetching LFS
# objects leaves a ~130-byte text pointer where the database should be, and the
# app would then build, start, and serve an empty network -- a broken demo that
# reports itself healthy. Fail the build here instead, with the fix in the
# message. Costs milliseconds and turns a silent deploy into a loud one.
RUN size=$(wc -c < data/crm.db); \
    if [ "$(head -c 15 data/crm.db)" != "SQLite format 3" ]; then \
        echo "FATAL: data/crm.db is a Git LFS pointer, not the database ($size bytes)." >&2; \
        echo "This host cloned the repository without fetching LFS objects." >&2; \
        echo "Fix: enable Git LFS for the build, or run 'git lfs install && git lfs pull' first." >&2; \
        exit 1; \
    fi; \
    echo "data/crm.db is a real SQLite file ($size bytes)"

# PORT is only the default, for a host that sets none. Render injects its own
# and it wins. Offline is not a fallback here, it is the contract: there is no
# key in this image and there is not meant to be one.
ENV PORT=7860 \
    LLM_OFFLINE=true \
    LLM_PROVIDER=gemini \
    DB_PATH=data/crm.db

# Documentation, and only for the default. The bind port is $PORT.
EXPOSE 7860

# Fails the container if the database did not come along, rather than serving
# an empty app that looks like a bug in the product. Reads PORT itself rather
# than assuming 7860, so the check follows the app to whatever port it bound.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request,json,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT','7860') + '/api/health')); sys.exit(0 if d['counts']['people'] else 1)"

# Shell form on purpose. Exec form does not expand ${PORT} -- it would hand
# uvicorn the literal string and the container would fail to start on Render.
CMD ["sh", "-c", "python -m uvicorn backend.app.api.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
