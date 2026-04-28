# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS base

# System deps: curl for healthcheck, build-essential only during pip install.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface

WORKDIR /app

# Install build deps in one layer, install Python deps, then drop build deps.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-download the embedding model into the image so first-request latency
# is fast and the container works without network egress to huggingface.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Drop build deps to keep the image small.
RUN apt-get purge -y --auto-remove build-essential && rm -rf /var/lib/apt/lists/*

# Application code.
COPY app ./app
COPY seed_data ./seed_data
COPY eval ./eval
COPY scripts ./scripts
COPY tests ./tests

EXPOSE 8000

# Healthcheck mirrors GET /health.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
