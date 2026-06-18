FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

COPY docker-socket-app/pyproject.toml docker-socket-app/uv.lock ./
COPY docker-socket-app/src ./src

FROM base AS production

ENV UV_NO_DEV=1

RUN uv sync --no-dev --compile-bytecode \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /run/docker-socket-app \
    && chown -R app:app /app /run/docker-socket-app

USER app

CMD ["uv", "run", "python", "-m", "docker_socket_app.main"]
