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

This is different from `infra/docs/NEW/mcp_log_server_architecture.md`, which
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
- when the planned database phase is implemented, that metadata should move
  into relational rows and the filesystem should keep only the raw saved log
  files

## Phase Status

Phase 1 is ready.

Phase 1 currently covers:

- FastMCP application skeleton
- JWT-protected per-request tool/resource access
- workflow bootstrap through `analyze_daily_log_bundle`
- on-demand workflow skill resources
- local Docker development and production compose paths
- HTTP integration tests and in-memory FastMCP client tests

Phase 2 should focus on deterministic collector-style data tools rather than
further reshaping the workflow bootstrap surface.

Current next-step note:

- an initial `collect_logs` tool now exists, so the next collection work should
  move toward snapshot inventory and retention rather than reworking the basic
  collection contract again.

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
3. MCP validates the requested project against the JWT `project_key`
4. MCP resolves the requested sources from the configured manifest
5. MCP writes the collection into a project-scoped logs root
6. MCP returns deterministic per-source collection results

Important collection response note:

- `collect_logs` returns `content: []`
- the real collection payload is in `result.structuredContent`
- the payload keeps both:
  - what the caller requested
  - what the server actually resolved from the manifest
- `tail_lines` is optional; if omitted, agents get a warning that full source
  output may be slow or large
- `DOCKER_LOGS_DIR` is treated as a logs root, not a flat one-run output path
- manifests and file-backed source logs are separate:
  - `MANIFEST_PATH` points to project manifest JSON files
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
- includes `app` and `tests` services

### Production Compose

- `docker-compose.prod.yml`

Characteristics:

- no bind mounts
- no file watching
- runs only the `app` service
- starts with `uv run python -m main`

## Current CI Test Path

Pull request test execution is wired through the shared reusable workflow from:

- `LukaszRemkowicz/ci-cd/.github/workflows/python-tests-uv.yml`

This repository provides the thin wrapper in:

- `.github/workflows/ci.yml`

The shared test job currently runs:

- `uv run pytest`

That covers both:

- in-memory FastMCP client tests
- JWT-protected HTTP integration tests

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
- `infra/docs/NEW/mcp_log_server_architecture.md`
  development/planning direction, not the primary current-state document
