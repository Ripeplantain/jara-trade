# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

FROM base AS deps
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy uv sync --frozen --no-dev

FROM base
RUN useradd --create-home --uid 1000 jara && mkdir /data && chown jara:jara /data
WORKDIR /app
COPY --from=deps /app/.venv ./.venv
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" JARA_DB_PATH=/data/jara.db
USER jara
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/market/status', timeout=2)"
# One worker only: prices live in an in-process cache shared by the SSE stream.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
