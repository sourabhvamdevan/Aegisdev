# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Non-root user for the app (defence in depth).
RUN useradd -m -u 10001 forge

WORKDIR /app

# System deps kept minimal. build tools only if a wheel needs compiling.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web
COPY eval ./eval
COPY .env.example ./.env.example

USER forge

EXPOSE 8000

# Render/most free hosts inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
