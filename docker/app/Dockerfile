FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends fail2ban \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY migrations ./migrations

FROM base AS development

RUN uv sync --frozen --compile-bytecode \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

USER app

CMD ["uv", "run", "python", "-m", "main"]

FROM base AS production

ENV UV_NO_DEV=1
ENV UV_FROZEN=1
ENV UV_NO_SYNC=1

RUN uv sync --frozen --no-dev --compile-bytecode \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app

USER app

CMD ["uv", "run", "python", "-m", "main"]
