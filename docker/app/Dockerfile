FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --extra dev

CMD ["uv", "run", "python", "-m", "main"]
