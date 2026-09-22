FROM python:3.13-slim

WORKDIR /app

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY config /app/config
COPY app /app/app

# Create a venv and install dependencies inside the image.
RUN uv venv /app/.venv && \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
