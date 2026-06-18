FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

COPY fail2ban-socket-app/pyproject.toml fail2ban-socket-app/uv.lock ./
COPY fail2ban-socket-app/src ./src

FROM base AS production

ENV UV_NO_DEV=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fail2ban \
    && rm -rf /var/lib/apt/lists/* \
    && uv sync --no-dev --compile-bytecode \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /run/fail2ban-socket-app \
    && chown -R app:app /app /run/fail2ban-socket-app

USER app

CMD ["uv", "run", "python", "-m", "fail2ban_socket_app.main"]
