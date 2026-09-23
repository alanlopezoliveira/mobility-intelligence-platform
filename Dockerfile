FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends make unrar-free ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY README.md .
COPY src ./src
COPY config ./config
COPY tests ./tests
COPY alembic.ini .
COPY alembic ./alembic

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000"]
