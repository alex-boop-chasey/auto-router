FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml /app/pyproject.toml
COPY config /app/config
COPY app /app/app

RUN uv sync --system --no-dev

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
