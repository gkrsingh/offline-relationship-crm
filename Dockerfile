# Hugging Face Spaces image.
#
# Three decisions worth stating, because each one is what keeps the demo from
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

# Spaces routes to 7860. Offline is not a fallback here, it is the contract:
# there is no key in this image and there is not meant to be one.
ENV PORT=7860 \
    LLM_OFFLINE=true \
    LLM_PROVIDER=gemini \
    DB_PATH=data/crm.db

EXPOSE 7860

# Fails the container if the database did not come along, rather than serving
# an empty app that looks like a bug in the product.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,json,sys; d=json.load(urllib.request.urlopen('http://127.0.0.1:7860/api/health')); sys.exit(0 if d['counts']['people'] else 1)"

CMD ["python", "-m", "uvicorn", "backend.app.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
