# Support Ops Assistant: RAG API + Part A ticket pipeline
#
#   docker build -t support-ops-assistant .                              # with embedding + re-ranking models
#   docker build -t support-ops-assistant --build-arg INSTALL_MODELS=false .   # slim, offline LSA embeddings
#   docker run -p 8000:8000 support-ops-assistant
#
# On start the container builds the ticket store (if missing), indexes the knowledge base
# (if missing) and serves the API on port 8000.

FROM python:3.12-slim

ARG INSTALL_MODELS=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROJECT_ROOT=/app \
    HF_HOME=/app/.cache/huggingface \
    PORT=8000

WORKDIR /app

# dependencies first, so code changes do not reinstall them
COPY requirements.txt requirements-models.txt ./
RUN pip install -r requirements.txt \
 && if [ "$INSTALL_MODELS" = "true" ]; then \
      pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-models.txt ; \
    fi

# download models at build time, so the running container needs no internet
RUN if [ "$INSTALL_MODELS" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')" ; \
    fi

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY data ./data
RUN pip install --no-deps . \
 && chmod +x scripts/docker-entrypoint.sh \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /app/warehouse \
 && chown -R app:app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
