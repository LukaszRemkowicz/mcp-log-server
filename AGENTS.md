# AGENTS.md

## Purpose

This file is the local working guide for the `mcp-log-server` repository.

Use it to understand:

- what this repository owns
- how the project is currently structured
- which MCP surfaces are already implemented
- how workflow prompts, skills, and deterministic tools are expected to work

This file is intentionally practical. It is not the full architecture source of
truth. For broader direction, also read:

- `README.md`
- `infra/docs/current_project_state.md`
- `infra/docs/repository_foundation.md`
- `infra/docs/analysis/mcp_log_server_architecture.md`
- `infra/scripts/README.md`
  Repository infra and deployment script runbook.
- `src/scripts/README.md`
  Typer command discovery and local developer command reference.


## Short Project Summary

This repository is building a dedicated FastMCP server that will gradually take
over deterministic log-collection and log-analysis support responsibilities
currently living around the `landingpage` collector and monitoring flow.

Important boundary:

- deterministic code gathers facts
- the LLM interprets facts

The MCP server is not meant to replace deterministic collection logic with free
LLM behavior.


## Current Repository Structure

Top-level directories:

- `src/`
  Main Python application code.
- `docker/`
  Docker image definition for the MCP app.
- `infra/`
  Repository-level infra and foundation notes.
- `infra/scripts/`
  Operational scripts and runbook notes for database backup/restore,
  production image builds, and production deploys.

Important current Python files:

- `src/main.py`
  Local entrypoint. Runs the real FastMCP HTTP server.
- `src/app.py`
  Creates the FastMCP app, attaches JWT auth, and imports MCP modules.
- `src/middleware/audit.py`
  MCP audit middleware for authenticated request logging.
- `src/database/services/`
  Database service wrappers for agent call and project manifest metadata.
- `src/settings.py`
  Environment-backed runtime settings.
- `src/auth/`
  JWT auth provider wiring and scope constants.
- `src/scripts/commands/generate_dev_jwt.py`
  Local development JWT generator.
- `src/manifests/`
  Manifest schema, loader, and bundled manifest data.
- `src/utils/assets.py`
  Loader for copied workflow assets under `src/agent_assets/`.
- `src/prompts/workflow.py`
  Prepared workflow prompt assembly.
- `src/skills/workflow.py`
  Workflow skill inventory and resource URIs.
- `src/resources/workflow.py`
  MCP resources for reading workflow skill content on demand.
- `src/tools/workflow.py`
  Workflow bootstrap tool.
- `src/tools/collection.py`
  Project discovery and deterministic log collection tools.
- `src/tools/snapshots.py`
  Snapshot inventory, file-read, and grep tools for persisted log snapshots.
- `src/tools/analysis.py`
  Snapshot analysis tools such as cleaned filtered views, grouped errors,
  incident bundles, and follow-up window suggestions.
- `src/services/log_filtering.py`
  Deterministic noise-filtering and cleaned-view generation based on
  manifest-selected profiles and source log shape.
- `src/utils/log_snapshots.py`
  Shared snapshot helpers for persistence metadata, timestamp parsing, read
  chunk selection, and snapshot error/result support.
- `src/tools/container_inspection.py`
  Approved read-only container inspection tools.
- `src/tools/system.py`
  MCP service diagnostics tools.
- `src/tests/`
  Current tests for manifests, MCP tools/resources, JWT-protected HTTP API
  behavior, and in-memory FastMCP client behavior.

Copied workflow assets:

- `src/agent_assets/prompts/`
- `src/agent_assets/skills/`
- `src/agent_assets/schemas/`
- `src/agent_assets/examples/`
- `src/agent_assets/tools/`

These assets were copied from `landingpage/backend/monitoring/agent_assets/`
and are the basis for mirroring the existing workflow behavior.


## Current MCP Surface

### Prompts

There is currently no first-class MCP prompt surface that the workflow depends on.

Design intent:

- keep workflow bootstrap structured
- keep prompt preparation separate from tool implementation code
- let the agent fetch prompt text, skill inventory, and tool inventory from one
  structured MCP tool call
- let the agent fetch only the skill resources it needs later

### Tools

Currently implemented:

- `analyze_daily_log_bundle`
- `collect_logs`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `create_filtered_view`
- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `list_projects`
- `read_container_file`
- `list_container_directory`
- `get_mcp_service_status`
- `get_mcp_health_check`

Purpose:

- `analyze_daily_log_bundle`
  returns structured workflow bootstrap data for the daily log-analysis flow:
  prepared prompt text, skill inventory, and visible tool inventory
- `collect_logs`
  returns deterministic collection results for one or more authorized
  projects and requested source keys from the manifest. Current agent-facing
  arguments are: `project_names`, `source_keys`, `workspace`, optional
  `session_id`, `since`, and `until`. For real MCP calls with
  `workspace="session"`, middleware creates a `session_id` when the request
  omits it; agents should reuse the returned `session_id` for later calls in
  the same investigation. The fixed `workflow-agent` token is not allowed to
  use `workspace="session"`; it must use `workspace="workflow"`.
- `list_log_snapshot_files`, `read_log_snapshot_file`, `grep_log_snapshot`
  operate on one persisted artifact identified by:
  - `session_id` + `project_name` for session investigations
  - `project_name` alone for the newest workflow artifact
  - `archive_name` + `project_name` for archived workflow artifacts
- `create_filtered_view`
  builds a cleaned deterministic view from one persisted raw artifact while
  keeping the raw collection as the source of truth
- `group_errors`, `build_incident_bundle`
  summarize one persisted snapshot for triage and follow-up analysis
- `suggest_followup_window`
  converts suspicious grouped timestamps into a narrower `collect_logs`
  `since` / `until` window
- `list_projects`
  returns the currently available manifest-backed projects with short project
  summaries and source inventory metadata
- `read_container_file`, `list_container_directory`
  expose approved read-only inspection inside manifest-bounded container paths
- `get_mcp_service_status`, `get_mcp_health_check`
  bootstrap/development diagnostics

Important:

- tools perform actions or prepare structured workflow data
- the LLM should not receive all skill content up front

### Resources

Resources are now a real part of the workflow surface for read-only skill
content.

Current pattern:

- tools prepare workflow bootstrap or perform deterministic actions
- resources expose on-demand skill content
- prompts are prepared in code and returned inside the workflow bootstrap tool

Current workflow skill resources are concrete resources such as:

- `skill://workflow/project_context`
- `skill://workflow/severity_guide`
- `skill://workflow/bot_detection`
- `skill://workflow/recommendations_guide`

`resources/templates/list` now returns the workflow skill template:

- `skill://workflow/{skill_name}`


## Manifest Model

The source manifest describes what log sources exist for a project and how MCP
should think about them.

Current file:

- `src/manifests/landingpage.json`

Current code:

- `src/manifests/models.py`
- `src/manifests/loader.py`

Manifest purpose:

- inventory of available project sources
- short project summary exposed to agent discovery
- routing metadata for deterministic collection, normalization, and noise
  filtering
- not the same thing as workflow prompts

Simple distinction:

- manifest = what exists
- manifest profiles = how deterministic normalization/filtering should route
- prompt/workflow = how the daily agent should think and operate


## Workflow Model

This repository should mirror the `landingpage` workflow pattern for daily log
analysis.

That means:

- one main workflow prompt
- separate on-demand skills
- deterministic tool/data retrieval outside the LLM

Do not collapse everything into one huge prompt bundle unless there is a very
clear reason.

Why:

- the `landingpage` workflow intentionally uses on-demand skill loading to save
  tokens
- that same principle should apply here


## Expected Agent Flow

For the daily workflow agent, the intended sequence is:

1. call `tools/call` for `analyze_daily_log_bundle`
2. send the prepared prompt plus skill inventory plus deterministic job context
   to the LLM
3. if the LLM wants a skill, call `resources/read` for the returned
   `skill://workflow/{skill_name}` URI
4. send the next LLM request with:
   - the main prompt
   - only the selected skill resource text(s)
   - deterministic log findings or later MCP-collected log data

Important distinction:

- generic MCP clients like Codex may start with discovery (`prompts/list`,
  `tools/list`)
- the fixed workflow agent should call the known workflow bootstrap tool


## What Is Not Done Yet

Major missing pieces:

- real JWT/Keycloak-backed auth
- final cross-repo rollout/integration behavior

Important current boundary:

- `collect_logs` always preserves a raw persisted snapshot
- cleaned analysis is a derived deterministic view built later through
  `create_filtered_view`
- grouped summaries and incident bundles build on those persisted/raw facts

Current auth state:

- FastMCP verifies JWTs per request
- component access is enforced with per-component `auth=` checks
- local development uses real example JWTs signed with the local shared secret
- local development JWTs are valid for 24 hours only
- before using local saved tokens, first check `updated_at` in a private local
  file such as `.agent/DEV_JWT_TOKENS.json`
- if `updated_at` is older than one day, treat the saved tokens as not valid
  and generate fresh ones with:
  `uv run commands generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json`
- if the expected token structure changes, for example a new required scope is
  added for MCP checks, treat previously saved tokens as outdated and generate
  fresh ones even if they are still within the 24-hour window
- after generating fresh tokens for local MCP checks, save the new values back
  into `.agent/DEV_JWT_TOKENS.json` before using curl or MCP client requests
- if you keep generated tokens in `.agent/DEV_JWT_TOKENS.json`, treat that file
  as developer-local state, not as a repository contract


## Planning Notes

Use these docs when a task touches near-term design work that is not yet fully
implemented:

- `infra/docs/analysis/mcp_log_server_architecture.md`
  broader MCP server implementation direction
- `infra/docs/analysis/log_search_and_large_log_handling.md`
  planned direction for adding log-search arguments to `collect_logs` and for
  handling large log payloads without relying on unbounded in-memory responses


## External Skills

This repo may also use the shared local skill library at:

- [antigravity-awesome-skills](/Users/lukaszremkowicz/Projects/antigravity-awesome-skills)

Use it as the first external skill source when a task needs:

- architecture review
- Python testing patterns
- code review guidance
- README/documentation authoring
- other reusable engineering workflows not already local to this repository

Do not copy skills into this repository by default. Prefer linking to and using
the shared skill set in place unless the user explicitly asks for a local copy.


## Working Rules For This Repo

- Prefer matching the existing `landingpage` monitoring workflow before
  inventing new abstractions.
- Check the shared local skill library in
  `/Users/lukaszremkowicz/Projects/antigravity-awesome-skills` before inventing
  new process, review, testing, or documentation guidance.
- Keep prompts small enough that on-demand skills still matter.
- Do not assume the agent runtime magically understands prompts/tools/skills;
  code still has to orchestrate MCP calls explicitly.
- Treat deterministic collection logic as the primary MCP responsibility.
- Treat workflow prompts as the LLM-facing layer built on top of deterministic
  facts.
- If docs and code diverge, code is the current truth, then update docs.


## Validation

Current local validation command:

```bash
uv run test
```

`uv run test` delegates to the Docker Compose `test` service. The test service
uses the separate `mcp_log_server_test` database, creates it when needed, and
runs `uv run migrate` before the full `uv run pytest` suite. DB-dependent
service tests are marked with `@pytest.mark.db` and run against the Compose
Postgres container using `Settings.db` from the test service `DATABASE_*`
settings. Do not point tests at the local app database.

Current collector test caveat:

- collector and snapshot tools have strong unit and API-level coverage
- docker-backed collection inside pytest is covered with mocks
- the curl-driven MCP HTTP path is covered by `infra/scripts/run_http_e2e.sh`,
  which recreates and migrates `mcp_log_server_test` before uploading temporary
  fixture manifests
- real live-container log collection is not yet exercised inside pytest

Common run path:

```bash
cd src
uv run python -m main
```

Docker development note:

- Docker Compose mounts `./src` into `/app/src`, so copied prompt/skill assets
  under `src/agent_assets/` are part of the live app volume.
- The compose app now watches the whole `/app/src` tree, not only `.py` files,
  so edits to prompt/skill/schema assets should also trigger reloads.
