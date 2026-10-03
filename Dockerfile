FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends make unrar-free libarchive-tools ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data /app/models /app/frontend/public \
    && chown -R app:app /app

COPY pyproject.toml constraints-rebuilt.txt README.md Makefile ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --upgrade pip \
    && pip install -c constraints-rebuilt.txt . \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000"]

# Batch preparation includes research models and the QA tools used by the
# historical forecasting-contract command. API serving uses the smaller target.
FROM runtime AS pipeline
USER root
RUN pip install -c constraints-rebuilt.txt '.[dev,research]'
COPY tests ./tests
COPY docs ./docs
RUN chown -R app:app /app
USER app
HEALTHCHECK NONE
CMD ["python", "scripts/rebuild_project.py", "--download"]
