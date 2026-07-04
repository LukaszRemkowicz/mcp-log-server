# mcp-log-server

Dedicated FastMCP service for deterministic log collection, filtering, snapshot
analysis, and bounded VPS/container inspection.

This repository is the implementation home for the MCP server described in
[infra/docs/current_project_state.md](infra/docs/current_project_state.md). It
keeps deterministic collection and analysis tooling separate from the LLM: code
gathers facts, the agent interprets them.

## What This Solves

This service gives AI agents a safe, repeatable way to inspect production logs
and container state without giving the agent direct server access.

Instead of asking an LLM to guess what happened from copied terminal output, the
MCP server collects the exact facts first:

- application and infrastructure logs
- saved log snapshots for later follow-up
- filtered and grouped error views
- approved read-only container and file inspection data
- runtime health details for Docker-backed services

The important boundary is simple:

- the MCP server gathers and stores facts
- the LLM reads those facts and explains what they mean

For reviewers, this repository shows the backend service that makes log
investigation reproducible, auditable, and safer than ad-hoc shell access. For
operators, it provides Docker Compose deployment paths, JWT-protected MCP
tools, database-backed audit metadata, and release scripts.

## Current Status

MVP-ready service foundation:

- FastMCP HTTP server with JWT-scoped tool/resource access.
- DB-backed caller authorization, project manifests, agent sessions, audit rows,
  and collected snapshot metadata.
- Deterministic log collection into workflow or session artifacts.
- Snapshot tools for listing, reading, grepping, filtered views, grouped errors,
  incident bundles, proxy activity, and follow-up windows.
- Manifest-bounded read-only container inspection tools.
- Local and production Docker Compose paths, production image hardening, backup,
  restore, build, deploy, and HTTP E2E scripts.

Current production hardening includes required database/JWT secrets, startup
rejection for known local placeholder secrets, non-root app containers,
frozen no-dev production dependency builds, Docker access through a separate
Unix-socket app, and authenticated MCP deploy health checks.

## Documentation Map

| Topic | Document |
| --- | --- |
| Current implemented state | [infra/docs/current_project_state.md](infra/docs/current_project_state.md) |
| Architecture and roadmap | [infra/docs/analysis/mcp_log_server_architecture.md](infra/docs/analysis/mcp_log_server_architecture.md) |
| Local setup, database runtime, migrations, manifest upload | [infra/docs/local_development.md](infra/docs/local_development.md) |
| Auth, logging, MCP HTTP settings, Docker socket/runtime config | [infra/docs/runtime_configuration.md](infra/docs/runtime_configuration.md) |
| Backup, restore, build, deploy overview | [infra/docs/operations.md](infra/docs/operations.md) |
| Detailed operational scripts runbook | [infra/scripts/README.md](infra/scripts/README.md) |
| MCP curl workflow and agent playbook | [infra/docs/mcp_workflow_playbook.md](infra/docs/mcp_workflow_playbook.md) |
| Tests, checks, CI, release validation | [infra/docs/quality_checks.md](infra/docs/quality_checks.md) |
| CLI command reference | [src/cli/README.md](src/cli/README.md) |
| Repository foundation notes | [infra/docs/repository_foundation.md](infra/docs/repository_foundation.md) |

## Tool Groups

The MCP tool surface is easier to understand as purpose-based groups. These are
documentation categories, not auth scopes.

| Group | Tools | Purpose |
| --- | --- | --- |
| Workflow bootstrap and discovery | `analyze_daily_log_bundle`, `list_projects` | Prepare daily workflow context and expose authorized project/source inventory. |
| Project manifest inspection | `read_project_manifest`, `explain_project_source` | Read the detailed persisted manifest contract and explain one source's configured producer provenance. |
| Log collection and session lifecycle | `collect_logs`, `close_agent_session` | Collect raw logs into workflow or session artifacts and close interactive session audit metadata. |
| Snapshot inventory and raw inspection | `list_log_snapshot_files`, `read_log_snapshot_file`, `grep_log_snapshot` | List, read, and search persisted raw snapshot files. |
| Snapshot analysis and derived views | `create_filtered_view`, `group_errors`, `build_incident_bundle`, `inspect_proxy_activity`, `suggest_followup_window` | Build deterministic cleaned views, grouped summaries, proxy diagnostics, incident bundles, and recollection windows. |
| Container inspection | `inspect_containers_health`, `inspect_container_detail`, `inspect_project_compose_state`, `inspect_project_runtime`, `inspect_project_deployment`, `stat_container_path`, `read_container_file`, `list_container_directory` | Inspect approved manifest-bounded containers, runtime Compose-labelled state, sanitized runtime env shape, deployment image provenance, and paths without mutating container state. |
| Host path inspection | `stat_project_path`, `read_project_file`, `list_project_directory` | Inspect approved manifest-bounded host file sources without arbitrary filesystem access or mutation. |
| Scheduler provenance | `inspect_project_scheduled_jobs` | Inspect configured read-only cron/systemd roots for project scheduler evidence without running or editing jobs. |
| VPS and edge diagnostics | `inspect_tls_certificate`, `inspect_traefik_tls_configuration` | Inspect the configured `SITE_DOMAIN` TLS certificate and sanitized Traefik router TLS state without accepting arbitrary hostnames, ports, raw Docker labels, or mutation requests. |
| MCP service diagnostics | `get_mcp_service_status`, `get_mcp_health_check` | Check MCP server/runtime health during development and operations. |

## Snapshot Storage And Cleanup

`collect_logs` saves raw log snapshots under the configured `LOGS_DIR`. These
files are the source of truth for later reads, searches, and analysis.

Workflow snapshots are shared per project. Each project has one current
`latest` snapshot. When a new workflow snapshot is collected, the previous
`latest` snapshot moves into that project's archive.

Archive cleanup runs the next time that same project is collected. Archives
older than `WORKFLOW_ARCHIVE_RETENTION` are removed together with their database
metadata, so snapshot tools do not point at files that no longer exist.

Session snapshots are separate from workflow snapshots. They are kept under the
session area in `LOGS_DIR` and old session folders are cleaned according to
`LOG_SNAPSHOT_RETENTION` when new session snapshots are prepared.

Filtered views, grouped errors, incident bundles, and proxy activity reports are
derived responses built from those raw snapshots. Collection diagnostics are
saved inside the same snapshot directory, so they follow the same cleanup
behavior as the logs they describe.

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
  socket-app/
    Dockerfile
socket-app/
  src/
  tests/
  README.md
docker-compose.yml
docker-compose.prod.yml
infra/docs/
  current_project_state.md
  local_development.md
  runtime_configuration.md
  mcp_workflow_playbook.md
  operations.md
  quality_checks.md
  analysis/
infra/scripts/
  README.md
  db_backup/
  release/
```

## Quick Start

Configuration is expected to come from environment variables injected by
Doppler. Reference variables are listed in [.env.example](.env.example), but the
runtime path should be Doppler rather than `env_file`.

Start the local database, MCP app, and Docker socket app:

```bash
doppler run -- docker compose up --build
```

Generate local JWTs for curl/manual MCP checks:

```bash
uv run command generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
export WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' .agent/DEV_JWT_TOKENS.json)"
export CODEX_AGENT_JWT="$(jq -r '.codex_agent' .agent/DEV_JWT_TOKENS.json)"
```

List visible MCP tools for one token:

```bash
curl -sS \
  -H "Authorization: Bearer $WORKFLOW_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":"tools","method":"tools/list","params":{}}' \
  http://127.0.0.1:8001/mcp
```

Run the test suite:

```bash
uv run test
```

## Common Commands

```bash
# Start only the local database
doppler run -- docker compose up -d db

# Apply committed migrations
uv run migrate

# Generate a new migration after reviewed model changes
uv run makemigrations <short_name>

# Upload all project manifests into the database
uv run command upload-project-manifest --all

# Update one existing project manifest
uv run command update-project-manifest --project landingpage

# Update all existing project manifests
uv run command update-project-manifest --all

# Update existing production manifests from the host
uv run command update-project-manifest --all

# Build a tagged production image
TAG=v1.2.3 infra/scripts/release/build.sh

# Deploy an already-built production image
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

Production release state is recorded under `/var/lib/mcp-log-server/prod`.
After a successful deploy records `current_tag`, host-side `uv run shell` and
`uv run command ...` helpers default `TAG` from that file when `TAG` is not
already set. Set `TAG=vX.Y.Z` explicitly to run a host-side command against a
specific production image before or outside the recorded deployment state.

## Socket App Access

The MCP app does not mount `/var/run/docker.sock`.

Docker-backed reads, CrowdSec diagnostics, and landingpage Django fixed
operations go through one small separate container named `socket-app`. That
container is the only application container with the real Docker socket
mounted. The MCP app talks to it over a private Unix socket file shared by a
Compose volume. There is no HTTP port and no TCP listener for helper access.

```text
MCP app
  -> /run/socket-app/gateway.sock
  -> socket-app
  -> Docker SDK
  -> /var/run/docker.sock
```

Compose passes the same socket path to both the MCP app and `socket-app`:

```text
/run/socket-app/gateway.sock
```

The named volume `socket-app-run` makes that directory visible to both
containers. The `socket-app` process creates the socket file there. The MCP app
only connects to that file.

Inside the MCP code, helper access is kept behind one transport client:
`DockerSocketGatewayClient`. Current users are:

- `LogCollectionService` for `collect_logs`
- `InspectionToolsService` for container, volume, Compose-state, and file-read
  inspection tools
- `CrowdSecService` for fixed `cscli` diagnostics inside the CrowdSec container
- `LandingpageDjangoService` for fixed landingpage Django command discovery and
  media inventory operations

```text
MCP app
  -> /run/socket-app/gateway.sock
  -> socket-app
  -> docker exec into the landingpage backend container
  -> uv run python manage.py mcp_list_commands --json
  -> uv run python manage.py media_inventory --json
  -> landingpage backend runtime
```

The landingpage Django discovery command is expected to return JSON describing
the available fixed commands and their agent-facing descriptions. The media
inventory command is expected to return JSON describing DB media references,
disk files, missing references, and unreferenced delete candidates. Until
landingpage provides those commands and the connector is pointed at the correct
backend container, the MCP tools report a connector or command availability
error rather than guessing from DB state.

The Docker socket app accepts only fixed read-only operations such as
`container_logs`, `container_health`, `container_file_read`, and
`vps_containers_inventory`. It does not accept arbitrary shell commands,
arbitrary Docker API calls, or mutation operations.

## Production Notes

Production deploys should run through Doppler and the release scripts. The app
fails fast when required production database/JWT secrets are missing or known
local placeholders are used.

Project manifests are data, not MCP application code. This repository may use
`src/manifests/projects` for local development examples, but production
manifests should come from the operational project that owns them, such as the
new `devops/` project. Configure `PROJECT_MANIFESTS_HOST_PATH` with that host
directory and Compose mounts it at `PROJECT_MANIFESTS_PATH` inside the app
container. The upload/update commands default to `PROJECT_MANIFESTS_PATH`, so
normal production usage does not need `--path`.

`inspect_project_compose_state` uses only manifest docker source targets and
current Docker runtime labels/metadata. It does not read Compose files or
validate desired image, port, mount, volume, or environment configuration.

`inspect_project_runtime` builds on that same read-only Docker path and returns
sanitized runtime configuration for the Compose-labelled project containers. It
exposes selected non-secret environment values and database host/port/name/user
shape when present, while reporting secret environment key presence without
returning secret values or raw environment dumps.

`inspect_project_deployment` compares optional manifest deployment metadata
with the Compose-labelled containers that are actually running. It reports
configured Compose file paths, current tag file status and value when present,
running image repositories/tags/digests, lifecycle timestamps, and mismatch
warnings without pulling, building, deploying, restarting, or exposing registry
credentials.

When `collect_logs` cannot collect a configured source, the project payload
includes `provenance_diagnostics` and the persisted
`collection_diagnostics.json` sidecar includes matching provenance hints. These
hints tell agents which read-only project tools to call next, such as
`explain_project_source`, scheduler inspection, file inspection, runtime
inspection, or deployment inspection, without making those tools prerequisites
for normal collection.

In production, file source paths must be written as paths visible inside the
MCP container. `docker-compose.prod.yml` mounts host `/var/log` as
`/host/var/log` and host `/etc/nginx/logs` as `/host/etc/nginx/logs`, so a host
log like `/var/log/app/app.jsonl` should be configured as
`/host/var/log/app/app.jsonl` in the manifest. Manifest targets are literal
absolute paths; date templates are not expanded. For dated log files, use a
stable current path, a host-side symlink/logrotate convention, or update the
manifest from the owning ops repository.

Production scheduler provenance is read-only and bounded. The app mounts the
host scheduler roots used by `inspect_project_scheduled_jobs` under `/host`,
including `/host/etc/cron.d`, `/host/etc/cron.daily`,
`/host/etc/cron.weekly`, `/host/var/spool/cron`, and
`/host/etc/systemd/system`. Override `SCHEDULER_INSPECTION_ROOTS` only when the
container-visible paths differ.

The production MCP app keeps helper access behind `socket-app`, using the same
Unix socket shape described above. On Linux hosts where the real Docker socket
group differs, pass `DOCKER_SOCKET_GID` so the `socket-app` container can read
`/var/run/docker.sock`; see
[infra/docs/runtime_configuration.md](infra/docs/runtime_configuration.md).

## Quality Checks

Use the repo wrapper for the full local validation path:

```bash
uv run test
```

Additional checks and CI/release behavior are documented in
[infra/docs/quality_checks.md](infra/docs/quality_checks.md).
