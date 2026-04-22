# mcp-log-server

Dedicated FastMCP service for deterministic log collection, filtering, and VPS
inspection.

This repository is the implementation home for the MCP server described in
[doc/mcp_log_server_architecture.md](doc/mcp_log_server_architecture.md).

## Current Status

Current repository foundation:

- Python application structure under `src/`
- minimal settings and Docker-first local bootstrap
- architecture docs and repository setup docs
- one basic smoke test for the settings scaffold

This repo does not yet implement project manifests or real log collection
parity with the existing collector.

Current auth is intentionally mocked behind an internal abstraction so the
final external auth provider can be connected later without reworking tool
logic.

The repository now includes a sample source manifest at
`manifests/landingpage.json`. This manifest is the project inventory/config
that later collection tools will consume after authorization selects the
project/resources.

## Layout

```text
src/
  app.py
  main.py
  settings.py
  tests/
docker/
  app/
    Dockerfile
docker-compose.yml
doc/
infra/docs/
```

## Local Development

Configuration is expected to come from environment variables injected by
Doppler.

Reference variables are listed in [.env.example](.env.example), but the runtime path should be Doppler rather than `env_file`.

Required variables:

- `ENVIRONMENT`
- `HOST`
- `PORT`
- `LOG_LEVEL`

Run the service through Docker Compose with Doppler:

```bash
doppler run -- docker compose up --build
```

The `app` service mounts `./src` into the container and reloads automatically
when Python files change.

The app container exposes a small HTTP endpoint on port `8001`:

- `GET /`
- `GET /healthz`
- `POST /echo`

Example manual requests live in
[http/requests.http](http/requests.http).

To inspect the status payload once the container is up:

```bash
curl -fsS http://127.0.0.1:8001/healthz
```

To run the test suite in Docker:

```bash
doppler run -- docker compose run --rm tests
```

If you prefer host execution while iterating, use `uv`:

```bash
uv sync --group dev
doppler run -- PYTHONPATH=src uv run python -m main
```

## Quality Checks

Install the local hooks:

```bash
uv run pre-commit install
```

Run all configured checks manually:

```bash
uv run pre-commit run --all-files
uv run pytest
docker compose config
docker compose build app tests
```

GitHub Actions is wired through the shared
[`LukaszRemkowicz/ci-cd`](https://github.com/LukaszRemkowicz/ci-cd) repository.
This repository keeps thin workflow wrappers, while the reusable CI/CD logic
lives there.

Current checks and release flows:

- pre-commit
- pytest
- Docker Compose validation
- Docker image build check
- CodeQL analysis on pull requests and the weekly schedule
- VERSION bump validation on `dev -> main` pull requests
- tag creation from `VERSION` on pushes to `main`

## AI Review

GitHub Copilot code review is configured on the GitHub side rather than through
this repository's workflows. Repository-specific guidance for Copilot lives in
[.github/copilot-instructions.md](.github/copilot-instructions.md).
