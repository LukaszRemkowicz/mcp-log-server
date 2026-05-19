# Quality Checks

## Test Suite

To run the test suite in Docker:

```bash
uv run test
```

`uv run test` delegates to `docker compose run --rm test`, which starts the
Compose database dependency, creates `mcp_log_server_test` when needed, runs
`uv run migrate` against that test database, then runs the full `uv run pytest`
suite inside the app test container. Tests that require the real database are
marked with `@pytest.mark.db`; the test container provides normal
`DATABASE_*` settings, and the tests use `Settings.db` to resolve the DSN.

If you prefer host execution while iterating, use `uv`:

```bash
uv sync --group dev
doppler run -- PYTHONPATH=src uv run python -m main
```

## Local Hooks And CI

Install the local hooks:

```bash
uv run pre-commit install
```

Run all configured checks manually:

```bash
uv run pre-commit run --all-files
uv run test
docker compose config
docker compose build app test
```

GitHub Actions is wired through the shared
[`LukaszRemkowicz/ci-cd`](https://github.com/LukaszRemkowicz/ci-cd) repository.
This repository keeps thin workflow wrappers, while the reusable CI/CD logic
lives there.

Current checks and release flows:

- pre-commit
- shared Python test workflow running `uv run pytest`
  - covers unit-style FastMCP client tests
  - covers JWT-protected HTTP integration tests
  - covers DB-marked service integration tests against the shared workflow
    Postgres service
  - applies committed migrations from the pytest DB setup before DB-marked
    tests run
  - covers docker-backed collection logic with mocks inside pytest
- curl-driven MCP HTTP end-to-end checks via `infra/scripts/run_http_e2e.sh`
  - runs against `mcp_log_server_test` by default and refuses database names
    that do not end in `_test`
  - recreates and migrates the test database before uploading temporary
    fixture manifests
- Docker Compose validation
- Docker image build check
- CodeQL analysis on pull requests and the weekly schedule
- VERSION bump validation on `dev -> main` pull requests

Important current caveat:

- real live-container log collection is not yet exercised inside pytest
- that runtime path is currently verified through the HTTP end-to-end script
  and local manual curl checks instead
- tag creation from `VERSION` on pushes to `main`
