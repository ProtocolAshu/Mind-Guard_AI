# MindGuard API. Build from the repository root:
#   docker build -f infra/docker/backend.Dockerfile -t mindguard-backend .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgomp1 is required by LightGBM and XGBoost at import time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY backend/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

# Application code, migrations and the trained model registry (loaded and SHA-256 verified at startup).
COPY backend/app /srv/app
COPY backend/migrations /srv/migrations
COPY backend/alembic.ini /srv/alembic.ini
COPY ml/models /srv/ml/models

ENV MODEL_DIR=/srv/ml/models \
    KNOWLEDGE_DIR=/srv/app/rag/corpus \
    PYTHONPATH=/srv

RUN useradd --system --uid 10001 --home /srv mindguard && chown -R mindguard:mindguard /srv
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
