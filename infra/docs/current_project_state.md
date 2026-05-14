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

- collected log bytes are still stored on disk under `LOGS_DIR`
- workflow artifact lookup is database-only through `CollectLogs` and
  `CollectLogsSource`; `workflow_inventory.json` is no longer written or read
- session artifacts also write `CollectLogs` and `CollectLogsSource` rows for
  follow-up tools
- `snapshot_metadata.json` is not a runtime metadata source; MCP tools use DB
  rows and DB file references for lookup

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
- `collect_logs` persistence through `CollectLogs` and `CollectLogsSource`
- DB-backed snapshot read, grep, and analysis tools
- HTTP integration tests and in-memory FastMCP client tests

Current next-step note:

- workflow/session artifact lookup is now DB-backed; upcoming collection work
  should build on the database contracts instead of adding filesystem metadata
  indexes.

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
- Phase 4e. Service Integration
  `collect_logs` writes DB metadata for saved artifacts, session ids are
  resolved by MCP/runtime code, and agent-call audit rows are persisted from
  middleware.
- Phase 4f. Remaining Runtime Integration
  Snapshot read/grep, analysis tools, project listing, API tests, and HTTP E2E
  paths use DB-backed metadata.

Current Phase 4 boundary:

- collect-log snapshot metadata models and migrations exist
- real DB tests now prove `CollectLogs`, `CollectLogsSource`, enum fields, JSON
  fields, relations, and custom `FileField` behavior against Postgres
- `collect_logs`, snapshot read/grep, and analysis tools use DB artifact rows
  as their runtime lookup source
- workflow lookup is DB-only; no workflow inventory JSON is written or read
- filesystem usage is limited to raw persisted log files referenced by DB file
  fields; do not add filesystem metadata fallback or bypass paths

## Current MCP Surface

### Tools

Currently implemented tools:

- `analyze_daily_log_bundle`
- `collect_logs`
- `close_agent_session`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `create_filtered_view`
- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `read_container_file`
- `list_container_directory`

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
- explicit agent-side session closing is implemented through
  `close_agent_session`; it marks audit metadata only and leaves snapshot files
  readable
- `tail_lines` is optional; if omitted, agents get a warning that full source
  output may be slow or large
- `LOGS_DIR` is treated as a logs root, not a flat one-run output path
- manifests and file-backed source logs are separate:
  - manifest JSON paths are passed directly to manifest upload/update commands
    with `--path`
  - runtime MCP tools read manifests from persisted database rows
  - manifest `file` source targets must be absolute paths, so each source
    declares its own filesystem location
- the current on-disk layout is:
  - `<LOGS_DIR>/workflow/<project_key>/latest/...`
  - `<LOGS_DIR>/workflow/<project_key>/archive/<timestamp>/...`
  - `<LOGS_DIR>/sessions/<session_id>/<project_key>/...`
- these paths are file storage locations only; artifact identity and lookup
  metadata live in the database

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
- runs curl-driven HTTP MCP E2E checks through `infra/scripts/run_http_e2e.sh`
  against `mcp_log_server_test`, not the local app database

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
