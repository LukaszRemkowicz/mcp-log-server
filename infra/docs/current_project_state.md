# MCP Log Server Current Project State

## Purpose

This document describes the current implemented state of the `mcp-log-server`
repository.

Use it as the stable project-facing reference for:

- current repository structure
- current MCP surface
- current auth model
- current workflow flow
- current local and production Docker paths

This is different from `infra/docs/analysis/mcp_log_server_architecture.md`, which
should be treated as a development/planning document for future direction.

## Current Repository Role

This repository currently owns the FastMCP server used for:

- workflow bootstrap for daily log analysis
- deterministic manifest-driven log collection
- on-demand workflow skill access through MCP resources
- JWT-protected MCP tool/resource access
- future deterministic collector-style log tooling

The key design boundary remains:

- deterministic code gathers facts
- the LLM interprets facts

Current persistence note:

- workflow and session artifacts are still filesystem-backed
- `snapshot_metadata.json` is a temporary filesystem metadata sidecar for the
  current non-database session implementation
- database model definitions and committed migrations now exist for audit rows,
  manifest rows, and future collect-log snapshot metadata rows, but MCP services
  do not write collect-log snapshot rows yet
- database service modules now wrap ORM access for agent call and project
  manifest metadata, but MCP tools do not call them yet
- when database-backed services are implemented, filesystem metadata must remain
  in place until a later explicit migration removes it as a source of truth

## Implementation Status

The repository currently includes:

- FastMCP application skeleton
- JWT-protected per-request tool/resource access
- workflow bootstrap through `analyze_daily_log_bundle`
- on-demand workflow skill resources
- local Docker development and production compose paths
- local PostgreSQL runtime wiring
- Tortoise ORM and Aerich migration configuration
- database migrations generated through the local `uv run makemigrations` alias
- backup and restore scripts for the local/prod metadata database
- prod build/deploy scripts following the landingpage release-script shape
- database service modules for agent call and project manifest metadata
- database models for agent calls, project manifests, collect-log artifacts, and
  collect-log artifact sources
- HTTP integration tests and in-memory FastMCP client tests

Current next-step note:

- an initial `collect_logs` tool now exists, so the next collection work should
  move toward snapshot inventory and retention rather than reworking the basic
  collection contract again.

## Current Phase Status

Phase tracking lives in
`infra/docs/analysis/mcp_log_server_architecture.md`. This document records only
the current implemented status.

Completed Phase 4 database-integration subphases:

- Phase 4a. Database Runtime
  Local/prod Postgres runtime wiring, database settings, Compose services, and
  persistent volumes are in place.
- Phase 4b. ORM And Models
  Tortoise ORM, Aerich migrations, database lifecycle wiring, and model modules
  are in place. Current models include `AgentCall`, `ProjectManifest`,
  `CollectLogs`, and `CollectLogsSource`.
- Phase 4c. Backup And Restore Policy Scripts
  Backup, restore, build, and deploy scripts are in place and documented.
- Phase 4d. Database Services
  Database service modules exist for agent call and project manifest metadata,
  with DB-marked service tests running against Compose Postgres.

Current Phase 4 boundary:

- collect-log snapshot metadata models and migrations exist
- real DB tests now prove `CollectLogs`, `CollectLogsSource`, enum fields, JSON
  fields, relations, and custom `FileField` behavior against Postgres
- `collect_logs`, snapshot read/grep, and analysis tools still use filesystem
  metadata as their runtime source of truth
- no MCP tool currently writes `CollectLogs` or `CollectLogsSource` rows

## Current MCP Surface

### Tools

Currently implemented tools:

- `analyze_daily_log_bundle`
- `collect_logs`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`

### Resources

Workflow skills are exposed as concrete read-only MCP resources, for example:

- `skill://workflow/project_context`
- `skill://workflow/severity_guide`
- `skill://workflow/recommendations_guide`
- `skill://workflow/bot_detection`

`resources/templates/list` currently exposes the workflow skill template:

- `skill://workflow/{skill_name}`

### Prompts

No MCP prompts are currently exposed.

The workflow bootstrap is tool-driven, not prompt-driven.

## Current Workflow Flow

Current intended agent sequence:

1. call `tools/call` for `analyze_daily_log_bundle`
2. read `result.structuredContent`
3. send prompt + skill inventory + deterministic context to the LLM
4. if the LLM needs a skill, call `resources/read` for the selected
   `skill://workflow/...` URI
5. send the selected skill text plus deterministic findings to the LLM

Important response note:

- `analyze_daily_log_bundle` currently returns `content: []`
- the real workflow payload is in `result.structuredContent`

## Current Collection Flow

Current deterministic collection shape:

1. call `tools/call` for `collect_logs`
2. include an optional requested project name and optional requested source keys
3. choose `workspace="workflow"` for the shared workflow snapshot or
   `workspace="session"` for an investigation workspace
4. the fixed `workflow-agent` token may only use `workspace="workflow"`
5. when `workspace="session"` omits `session_id`, MCP creates one before the
   tool runs and returns it to the agent
6. MCP validates the requested project against the JWT `project_key`
7. MCP resolves the requested sources from the configured manifest
8. MCP writes the collection into a project-scoped logs root
9. MCP returns deterministic per-source collection results

Important collection response note:

- `collect_logs` returns `content: []`
- the real collection payload is in `result.structuredContent`
- the payload keeps both:
  - what the caller requested
  - what the server actually resolved from the manifest
- session collection payloads include the effective `session_id`; agents reuse
  that value for follow-up collection, read, grep, and analysis calls in the
  same investigation
- explicit agent-side session closing is not implemented yet
- `tail_lines` is optional; if omitted, agents get a warning that full source
  output may be slow or large
- `DOCKER_LOGS_DIR` is treated as a logs root, not a flat one-run output path
- manifests and file-backed source logs are separate:
  - `MANIFEST_PATH` points to project manifest JSON files used by manifest
    upload/update commands
  - runtime MCP tools read manifests from persisted database rows
  - relative manifest `file` source targets resolve under `FILE_SOURCE_ROOT`
  - if `FILE_SOURCE_ROOT` is omitted, it defaults to the sibling `logs/`
    directory next to `MANIFEST_PATH`
- the current on-disk layout is:
  - `<DOCKER_LOGS_DIR>/<project_key>/latest/...`
  - `<DOCKER_LOGS_DIR>/<project_key>/archive/<timestamp>/...`

## Current Auth Model

The server currently uses FastMCP JWT verification per request.

Current implemented shape:

- `JWTVerifier` validates incoming bearer tokens
- tool/resource visibility is enforced with per-component `auth=` checks
- local development uses example JWTs signed with the local shared secret

This is a development-ready JWT flow, not a final Keycloak production rollout.

## Current Manifest Model

The current bundled sample manifest is:

- `src/manifests/projects/landingpage.json`

This is runtime project inventory/config for future collector-style tools.

It is not a prompt and not a workflow payload.

## Current Docker Paths

### Development Compose

- `docker-compose.yml`

Characteristics:

- bind-mounts `./src`
- uses `watchfiles`
- includes `app`, `db`, and `tests` services
- uses the official `postgres:18` image for the `db` service
- persists local database data in the named `postgres-data` Docker volume
- binds published service ports to `127.0.0.1` to avoid VPS-wide port exposure
- uses host port `5437` for local MCP Postgres by default, leaving
  `landingpage`'s local `5436` binding separate
- runs the `test` service against the separate `mcp_log_server_test` database,
  not the local app database

### Production Compose

- `docker-compose.prod.yml`

Characteristics:

- no bind mounts
- no file watching
- runs `app` and `db` services
- uses the official `postgres:18` image for the `db` service
- persists database data in the named `postgres-data` Docker volume
- binds the MCP HTTP host port to `127.0.0.1`
- starts with `uv run python -m main`

## Current CI Test Path

Pull request test execution is wired through the shared reusable workflow from:

- `LukaszRemkowicz/ci-cd/.github/workflows/python-tests-uv.yml`

This repository provides the thin wrapper in:

- `.github/workflows/ci.yml`

The shared test job enables the reusable workflow's Postgres service and runs:

- `uv run pytest`

The pytest DB setup applies committed migrations before DB-marked tests run, so
the repository does not need a custom shared workflow test command.

That covers both:

- in-memory FastMCP client tests
- JWT-protected HTTP integration tests
- DB-marked service integration tests against the shared workflow Postgres
  service

Local pre-commit uses `uv run test`, which delegates to the Docker Compose test
service and applies committed migrations before the full pytest suite.

## Document Boundary

Use these docs with the following intent:

- `README.md`
  day-to-day setup, env vars, playbook, commands
- `AGENTS.md`
  local working guide for engineering sessions
- `infra/docs/current_project_state.md`
  stable current-state reference
- `infra/docs/repository_foundation.md`
  what the repository foundation delivered
- `infra/docs/analysis/mcp_log_server_architecture.md`
  development/planning direction, not the primary current-state document
