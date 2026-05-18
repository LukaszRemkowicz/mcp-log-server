# mcp-log-server

Dedicated FastMCP service for deterministic log collection, filtering, and VPS
inspection.

This repository is the implementation home for the MCP server described in
[infra/docs/current_project_state.md](infra/docs/current_project_state.md).

Operational database backup/restore and prod build/deploy scripts are documented
in [infra/scripts/README.md](infra/scripts/README.md).

## Current Status

Current repository foundation:

- Python application structure under `src/`
- minimal settings and Docker-first local bootstrap
- architecture docs and repository setup docs
- FastMCP tool/resource workflow bootstrap
- JWT-protected HTTP integration tests and in-memory FastMCP client tests

This repo does not yet implement real log collection parity with the existing
collector.

The repository now includes project source manifests under
`src/manifests/projects/`. These manifests are the project inventory/config
that later collection tools consume after authorization selects the
project/resources.

The repository also now includes a copied MCP-owned monitoring asset bundle
under `src/agent_assets/`.

Current MCP workflow surface includes:

- tools: `analyze_daily_log_bundle`, `collect_logs`, `close_agent_session`, `list_log_snapshot_files`, `read_log_snapshot_file`, `grep_log_snapshot`, `create_filtered_view`, `group_errors`, `build_incident_bundle`, `inspect_proxy_activity`, `suggest_followup_window`, `list_projects`, `get_mcp_service_status`, `get_mcp_health_check`, `inspect_containers_health`, `inspect_container_detail`, `stat_container_path`, `read_container_file`, `list_container_directory`
- resources: concrete workflow skill resources such as
  `skill://workflow/project_context`, `skill://workflow/severity_guide`,
  `skill://workflow/bot_detection`
- prompts: none exposed right now

### Tool Groups

The tool surface is easier to understand as purpose-based groups. These groups
are documentation categories, not auth scopes.

| Group | Tools | Purpose |
| --- | --- | --- |
| Workflow bootstrap and discovery | `analyze_daily_log_bundle`, `list_projects` | Prepare the daily workflow prompt/tool inventory and expose authorized project/source inventory. |
| Log collection and session lifecycle | `collect_logs`, `close_agent_session` | Collect raw logs into workflow or session artifacts and close interactive session audit metadata. |
| Snapshot inventory and raw inspection | `list_log_snapshot_files`, `read_log_snapshot_file`, `grep_log_snapshot` | List, read, and search persisted raw snapshot files after collection. |
| Snapshot analysis and derived views | `create_filtered_view`, `group_errors`, `build_incident_bundle`, `inspect_proxy_activity`, `suggest_followup_window` | Build deterministic cleaned views, grouped summaries, proxy activity diagnostics, incident bundles, and recollection windows from an already-collected snapshot. |
| Container inspection | `inspect_containers_health`, `inspect_container_detail`, `stat_container_path`, `read_container_file`, `list_container_directory` | Inspect approved manifest-bounded containers and paths without mutating container state. |
| MCP service diagnostics | `get_mcp_service_status`, `get_mcp_health_check` | Check MCP server/runtime health during development and operations. |

The daily workflow mirrors the current `landingpage` monitoring pattern:
`analyze_daily_log_bundle` returns structured workflow data with:

- prepared prompt text
- available workflow skill resources
- available workflow tools

Then the workflow agent can separately load only the skill resources it needs
before sending the final assembled input to the LLM with deterministic
findings.

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
docker-compose.prod.yml
infra/docs/
  current_project_state.md
  analysis/
  repository_foundation.md
infra/scripts/
  README.md
  db_backup/
  release/
```

## Local Development

Configuration is expected to come from environment variables injected by
Doppler.

Reference variables are listed in [.env.example](.env.example), but the runtime path should be Doppler rather than `env_file`.

All settings currently have development defaults in code, so the server can
start locally without explicitly setting every variable.

For real deployment, some values should still be treated as required.

Production-required secrets/config:

- `ENVIRONMENT`
- `HOST`
- `PORT`
- `JWT_SHARED_SECRET`
- `JWT_ISSUER`
- `JWT_AUDIENCE`
- `MCP_PATH`
- `MCP_STATELESS_HTTP`
- `MCP_JSON_RESPONSE`
- `DATABASE_HOST`
- `DATABASE_PORT`
- `DATABASE_NAME`
- `DATABASE_USER`
- `DATABASE_PASSWORD`
- `TAG`

Production-recommended runtime config:

- `LOG_LEVEL`
- `LOG_FORMAT`
- `JWT_ALGORITHM`
- `JWT_EXPIRATION_SECONDS`
- `MCP_PORT_HOST` when the host-side MCP port should differ from `8001`

Local development defaults:

- all of the above have defaults in [src/settings.py](/Users/lukaszremkowicz/Projects/mcp-log-server/src/settings.py:1)
- local development can run without explicitly setting every variable
- production should not rely on the built-in JWT defaults, especially
  `JWT_SHARED_SECRET=change-me-local-dev-secret`

### Database Runtime Configuration

The repository has local and production PostgreSQL runtime wiring, Tortoise ORM
configuration, initial model definitions, and an initial database migration.
Future migration files should be generated only after the related model changes
are reviewed and approved.

- `DATABASE_HOST`
  Database host used by app code.
  Default: `127.0.0.1`

  Docker Compose injects `db` for app/test containers so they connect over the
  Compose network.

- `DATABASE_PORT`
  Database port used by app code.
  Default: `5432`

- `DATABASE_PORT_HOST`
  Host port exposed by the local Compose `db` service.
  Default: `5437`

- `DATABASE_NAME`
  PostgreSQL database name.
  Default: `mcp_log_server`

  The Docker Compose `test` service overrides this to
  `mcp_log_server_test`, so DB tests do not flush or mutate the local app
  database.

- `DATABASE_USER`
  PostgreSQL application user.
  Default: `mcp_log_server`

- `DATABASE_PASSWORD`
  PostgreSQL application password.
  Default: `mcp-log-server-local-password`

- `FAIL2BAN_SOCKET_PATH`
  Path where the MCP app expects the fail2ban Unix socket inside the app
  container.
  Default: `/var/run/fail2ban/fail2ban.sock`

  Live `inspect_live_fail2ban_activity` diagnostics call
  `fail2ban-client -s "$FAIL2BAN_SOCKET_PATH" ...`. This is separate from
  collected fail2ban logs; the live command only works when the host socket is
  intentionally mounted into the MCP container.

- `FAIL2BAN_SOCKET_DIR_HOST`
  Host path to the fail2ban Unix socket directory when using the optional fail2ban Compose
  override.
  Default: `/var/run/fail2ban`

The Compose files run PostgreSQL through the official `postgres:18` image.
Database files are stored in the named `postgres-data` Docker volume, so data
persists when containers are recreated.

The local Compose file uses a plain local build without an explicit app image
tag. Production uses the same landingpage-style contract as
`${ENVIRONMENT}-mcp-log-server:${TAG}`; set `ENVIRONMENT=prod` and a release
`TAG` for production runs.

Host port bindings are loopback-only to keep the MCP stack safe beside
`landingpage` on the same VPS:

- MCP HTTP: `127.0.0.1:${MCP_PORT_HOST:-8001}->8001`
- MCP local Postgres: `127.0.0.1:${DATABASE_PORT_HOST:-5437}->5432`

Inside Docker, services should use service DNS names such as `db`, not static
container IP addresses. Cross-repository integration should later use an
explicit shared network or reverse-proxy route rather than hard-coded Docker
IPs.

Start the local database and app together:

```bash
doppler run -- docker compose up --build
```

The local `app` service applies committed Aerich migrations with
`uv run migrate` before starting the FastMCP server. If migrations fail, the
app exits instead of starting against a stale schema.

Start only the local database:

```bash
doppler run -- docker compose up -d db
```

Reset local database data:

```bash
doppler run -- docker compose down --volumes
doppler run -- docker compose up -d db
```

### ORM Configuration

Tortoise ORM configuration lives in [src/database/config.py](/Users/lukaszremkowicz/Projects/mcp-log-server/src/database/config.py:1).
Database models live in [src/database/models.py](/Users/lukaszremkowicz/Projects/mcp-log-server/src/database/models.py:1).
Database service wrappers live under [src/database/services/](/Users/lukaszremkowicz/Projects/mcp-log-server/src/database/services).
Database startup/shutdown helpers live in [src/database/lifecycle.py](/Users/lukaszremkowicz/Projects/mcp-log-server/src/database/lifecycle.py:1).

The migration tool is Aerich, configured in [pyproject.toml](/Users/lukaszremkowicz/Projects/mcp-log-server/pyproject.toml:62)
with:

```toml
[project.scripts]
commands = "scripts.main:main"
makemigrations = "database.cli:makemigrations"
migrate = "database.cli:migrate"
shell = "scripts.shell:main"

[tool.aerich]
tortoise_orm = "database.config.TORTOISE_ORM"
location = "./migrations"
src_folder = "./src"
```

Typer command documentation lives in
[src/scripts/README.md](/Users/lukaszremkowicz/Projects/mcp-log-server/src/scripts/README.md).

Generate new migration files only after the related model structure has been
reviewed and approved.

After model approval, create and apply migrations from the repository root.
For local host commands, the aliases default to the Compose-published database
port `127.0.0.1:${DATABASE_PORT_HOST:-5437}` when `DATABASE_HOST` and
`DATABASE_PORT` are not already set:

```bash
docker compose up -d db
uv run makemigrations initial
uv run migrate
```

`uv run makemigrations` delegates to `aerich migrate` and writes migration
files. On the first run against a fresh database, it falls back to
`aerich init-db` when Aerich reports that initialization is required.
`uv run migrate` delegates to `aerich upgrade` and applies already generated
migration files.

For later model changes, pass a short custom name as the positional suffix:

```bash
uv run makemigrations remove_agent_call_redundant_fields
uv run migrate
```

The wrapper slugifies the suffix, passes it to Aerich as `--name`, and
normalizes Aerich timestamp filenames into the project style, for example
`003_rename_agent_call_client_to_caller.py`. Aerich's native name option still
works too:

```bash
uv run makemigrations --name "rename agent call client to caller"
```

Review generated files under `migrations/` before committing them. Production
deployments should apply already-committed migrations with `aerich upgrade`;
they should not generate new migration files on the server.

Open a Django `shell_plus`-style developer shell:

```bash
uv run shell
```

The shell initializes Tortoise ORM and preloads the database models, database
services, application services, `settings`, and `TORTOISE_ORM`. Use top-level
`await` for ORM calls, for example:

```python
await AgentCall.objects.all().limit(5)
await CollectLogs.objects.filter(project_name="landingpage")
```

Upload configured project manifests into the database:

```bash
uv run commands upload-project-manifest --path src/manifests/projects landingpage
uv run commands upload-project-manifest --path src/manifests/projects --all
```

Upload is create-only. Existing project manifests are reported and left
untouched. To update an existing manifest, run:

```bash
uv run commands update-project-manifest --path src/manifests/projects --project landingpage
```

Run this command on the host where the Docker Compose app service is running.
It uses Docker SDK to execute a hidden internal command inside the app
container, so database access uses Docker service DNS (`db:5432`) instead of
host `127.0.0.1:5432`. The command reads manifests from `--path`; when omitted,
`--path` defaults to the current working directory (`.`).

Runtime MCP tools read project manifests from the database. Manifest JSON files
are source input for the upload/update commands, not runtime app settings and
not the lookup path for `collect_logs`, `list_projects`, or manifest-backed
analysis.

### Database Backup, Restore, Build, And Deploy

Operational scripts live in [infra/scripts/](/Users/lukaszremkowicz/Projects/mcp-log-server/infra/scripts/README.md:1).
They support only `local` and `prod`; this repository does not have a staging
environment.

Create a local database backup:

```bash
ENVIRONMENT=local infra/scripts/db_backup/backup_db.sh
```

Restore a local database backup:

```bash
ENVIRONMENT=local infra/scripts/db_backup/restore_db.sh .agent/backups/db/local/<backup>.dump
```

Build the tagged production image:

```bash
TAG=v1.2.3 infra/scripts/release/build.sh
```

The build script refuses to build with uncommitted changes unless
`EMERGENCY=true` is set.

Deploy the already-built production image:

```bash
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

For build and deploy, `TAG` may be provided through the environment. If it is
omitted, the scripts use the exact Git tag checked out in the working tree.

The deploy script verifies the local image, creates a DB backup by default,
applies committed migrations with `uv run migrate`, starts the app service, and
checks that the app accepts TCP connections inside the container. It asks for
confirmation before mutating the target stack unless `AUTO_APPROVE=true`, and
starts the app with `--force-recreate` so the selected image is rerun. Use
`SKIP_BACKUP=true` or `SKIP_MIGRATE=true` only for an intentional
operator-controlled run.

### Auth Configuration

The server now uses FastMCP's HTTP auth layer, so tool visibility and tool
calls are evaluated per bearer token, not once at process startup.

Tool calls also pass through the `mcp_callers` database allowlist. FastMCP
still validates the JWT signature, issuer, audience, expiration, and scopes
first. After that, middleware checks for one manual row matching:

- `client_id`
- `client_type`
- `workspace` (`workflow` or `session`)

The row also stores `allowed_projects` as a JSON list of project names. That
database list becomes the effective project allowlist for the tool call, so a
valid JWT is not enough by itself; the caller must also have a matching
`mcp_callers` row for the requested workspace and projects.

The concrete caller model is resolved through `MCP_CALLER_MODEL`, which defaults
to `database.models.McpCaller`.

Example manual row:

```sql
INSERT INTO mcp_callers (
    client_id,
    client_type,
    workspace,
    allowed_projects
)
VALUES (
    'codex-agent',
    'codex',
    'session',
    '["landingpage"]'::jsonb
);
```

- `JWT_ALGORITHM`
  Signing algorithm for local example JWTs.
  Default: `HS256`

- `JWT_SHARED_SECRET`
  Shared secret used to sign and verify local example JWTs.
  Default: `change-me-local-dev-secret`

- `JWT_ISSUER`
  Required `iss` claim for local example JWTs.
  Default: `mcp-log-server-dev`

- `JWT_AUDIENCE`
  Required `aud` claim for local example JWTs.
  Default: `mcp-log-server`

- `JWT_EXPIRATION_SECONDS`
  Lifetime of locally generated example JWTs.
  Default: `86400`

Generate example JWTs locally:

```bash
uv run commands generate-dev-jwt
```

That prints a JSON payload with:

- `workflow_agent`
- `codex_agent`
- `created_at`
- `updated_at`

The usual local flow is to save it into `.agent/DEV_JWT_TOKENS.json`:

```bash
uv run commands generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

When `--output-file` is provided, the command writes the token JSON to that
path instead of printing tokens to the console. Parent directories are created
automatically. Without `--output-file`, the JSON is printed to stdout.

The command also accepts explicit identity claim overrides when you need
tokens for a different local caller:

```bash
uv run commands generate-dev-jwt \
  --codex-client-id local-codex \
  --codex-client-type codex
```

Then export the values you want to use with `curl`:

```bash
export WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' .agent/DEV_JWT_TOKENS.json)"
export CODEX_AGENT_JWT="$(jq -r '.codex_agent' .agent/DEV_JWT_TOKENS.json)"
```

Current example JWT capabilities:

- `workflow_agent`
  - `create_filtered_view`
  - `group_errors`
  - `build_incident_bundle`
  - `suggest_followup_window`
  - `collect_logs`
  - `list_log_snapshot_files`
  - `read_log_snapshot_file`
  - `grep_log_snapshot`
  - `list_projects`
  - `analyze_daily_log_bundle`
  - `get_mcp_service_status`
  - `get_mcp_health_check`
  - `resources/read` for `skill://workflow/{skill_name}`

- `codex_agent`
  - `create_filtered_view`
  - `group_errors`
  - `build_incident_bundle`
  - `suggest_followup_window`
  - `collect_logs`
  - `list_log_snapshot_files`
  - `read_log_snapshot_file`
  - `grep_log_snapshot`
  - `list_projects`
  - `get_mcp_service_status`
  - `get_mcp_health_check`
  - `inspect_containers_health`
  - `inspect_container_detail`
  - `read_container_file`
  - `list_container_directory`
  - `close_agent_session`

Important:

- tools are registered once in code
- tool visibility is filtered per request from the presented bearer token
- local development now uses real JWT-shaped bearer tokens
- this is still a dev-only shared-secret setup; later real JWT auth can replace
  the signing/verification source without changing the tool contracts

### Logging Configuration

The project now has a small application-owned logger in addition to FastMCP's
own HTTP server logging.

- `LOG_LEVEL`
  Controls the project logger level.
  Default: `INFO`

  Typical values:

  - `DEBUG`
  - `INFO`
  - `WARNING`
  - `ERROR`

- `LOG_FORMAT`
  Controls the project log output format.
  Default: `text`

  Supported values:

  - `text`
    human-readable development logs
  - `json`
    one JSON object per line for easier ingestion by log pipelines later

Current project logs include:

- startup of the FastMCP HTTP service
- MCP tool registration
- workflow tool calls such as:
  - `analyze_daily_log_bundle`
  - `get_mcp_service_status`
  - `get_mcp_health_check`

Example:

```bash
LOG_LEVEL=DEBUG LOG_FORMAT=text doppler run -- docker compose up --build
```

### MCP Configuration

These variables control how the local FastMCP HTTP server starts.

Manifests and logs are intentionally separate:

- manifest JSON paths are passed to `uv run commands upload-project-manifest`
  and `uv run commands update-project-manifest` with `--path`
- runtime MCP tools read persisted manifest rows from the database
- file-backed manifest source targets must be absolute paths, so each source
  declares exactly where its log file lives

- `MCP_PATH`
  HTTP path where the FastMCP endpoint is exposed.
  Default: `/mcp`

  If this is set to `/mcp`, MCP JSON-RPC requests go to:

  - `http://127.0.0.1:8001/mcp`

  If changed to `/api/mcp`, the endpoint becomes:

  - `http://127.0.0.1:8001/api/mcp`

- `MCP_STATELESS_HTTP`
  Enables stateless HTTP mode for the FastMCP transport.
  Default: `true`

  In the current local setup this means the server treats each HTTP request as
  self-contained. That fits the current curl-based usage and simple agent
  integration we are building now.

- `MCP_JSON_RESPONSE`
  Forces FastMCP to return JSON responses over HTTP.
  Default: `true`

  In practice this is why requests such as `tools/call` return JSON-RPC
  payloads and why clients should send:

  - `Accept: application/json`

  Without that header FastMCP can reject the request as not acceptable.

Run the service through Docker Compose with Doppler:

```bash
doppler run -- docker compose up --build
```

The local `app` service applies committed migrations on startup, then mounts
`./src` into the container and reloads automatically when files under `src/`
change, including copied workflow assets such as prompts, skills, schemas, and
examples.

The local Compose stack also starts a `db` service and stores its data in the
named `postgres-data` volume.

For a production-like container run without bind mounts or file watching, use
the dedicated production compose file:

```bash
doppler run -- docker compose -f docker-compose.prod.yml up --build -d
```

The production deploy script includes the fail2ban socket override by default,
so the normal VPS path is still:

```bash
doppler run -- TAG=v1.2.3 infra/scripts/release/deploy.sh
```

If the VPS should deploy without live fail2ban socket access, disable the
override explicitly:

```bash
doppler run -- ENABLE_FAIL2BAN_SOCKET=false TAG=v1.2.3 infra/scripts/release/deploy.sh
```

Then verify from inside the app container:

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.fail2ban.yml \
  exec app fail2ban-client -s /var/run/fail2ban/fail2ban.sock status
```

Production compose differences:

- runs the `app` and `db` services
- does not mount the local source tree
- does not use `watchfiles`
- starts the server with `uv run python -m main`

The app container exposes the MCP HTTP endpoint on port `8001`:

- `POST /mcp`

Example manual requests live in
[src/tests/requests.http](/Users/lukaszremkowicz/Projects/mcp-log-server/src/tests/requests.http).

To inspect the structured workflow bootstrap once the container is up:

```bash
curl -fsS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"analyze_daily_log_bundle","arguments":{}}}' \
  http://127.0.0.1:8001/mcp
```

## Workflow Playbook

This section describes the current MCP workflow surface for the daily
log-analysis agent.

Important response note:

- FastMCP tool results can return both `content` and `structuredContent`
- for agent code, use `result.structuredContent`
- `analyze_daily_log_bundle` currently returns `content: []` and puts the real
  payload only in `structuredContent`
- current workflow entrypoint is a tool, not an MCP prompt
- current workflow skills are exposed as concrete resources, not resource templates

For local development, generate fresh example JWTs with:

```bash
uv run commands generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

Refresh them when:

- they are older than 24 hours
- token structure or expected scopes change

Then export them into your shell:

```bash
export WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' .agent/DEV_JWT_TOKENS.json)"
export CODEX_AGENT_JWT="$(jq -r '.codex_agent' .agent/DEV_JWT_TOKENS.json)"
```

Quick terminal helpers:

```bash
# Pretty-print any MCP JSON body
curl -k -sS http://127.0.0.1:8001/mcp ... | jq

# Print only file content from read_container_file
curl -k -sS http://127.0.0.1:8001/mcp ... | jq -r '.result.structuredContent.content'

# Print only discovered entry names from list_container_directory
curl -k -sS http://127.0.0.1:8001/mcp ... | jq -r '.result.structuredContent.entries[].name'
```

### 1. List Visible Tools

Use this to see which tools are visible for the current JWT.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"1",
    "method":"tools/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it is for:

- confirm JWT-scoped tool visibility
- inspect the current MCP tool surface before calling anything

What it returns right now for the workflow token:

- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `collect_logs`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `analyze_daily_log_bundle`

What it returns right now for the codex token:

- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `collect_logs`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `inspect_containers_health`
- `inspect_container_detail`
- `read_container_file`
- `list_container_directory`

### 2. Get Workflow Bootstrap

Use this as the first workflow-agent call.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2",
    "method":"tools/call",
    "params":{
      "name":"analyze_daily_log_bundle",
      "arguments":{}
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

What it is for:

- first workflow-agent bootstrap step
- returns the main workflow prompt
- returns the available workflow skills
- returns the available workflow tools

What it returns:

- `workflow_name`
- `prompt`
- `mandatory_skills`
- `optional_skills`
- `tools`

### 2a. List Available Projects

Use this to discover which manifest-backed projects are currently available to
the current JWT.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2a",
    "method":"tools/call",
    "params":{
      "name":"list_projects",
      "arguments":{}
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent.result'
```

What it returns:

- `project_name`
- `project_summary`
- `source_keys`

### 2b. Collect Deterministic Logs

Use this when the agent needs manifest-driven log collection for the
authorized project and selected sources.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["nginx","backend"],
        "workspace":"workflow",
        "since":"30m"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

What it is for:

- first deterministic collection surface after the workflow skeleton
- explicit project, source, workspace, and docker/file option tracking
- manifest-driven source resolution before later snapshot reads or searches
- project-scoped snapshot persistence under the configured logs root

Agent-facing collect_logs arguments:

- `project_names`
- `source_keys`
- `workspace`
- optional `session_id`
- `since`
- `until`

Important:

- if `since` is omitted, the server defaults to `24h`
- if `source_keys` is omitted, the server behaves as if `source_keys=["all"]`
- `collect_logs` now always persists per-project artifacts for the requested workspace
- `workspace="workflow"` does not require `session_id`
- `workspace="session"` creates a server-generated `session_id` when the request omits it
- the fixed `workflow-agent` token may only use `workspace="workflow"`; use a
  non-workflow agent token for interactive `workspace="session"` investigations
- use the returned `session_id` for later calls in the same investigation
- reuse the returned `session_id` when the investigation later needs logs from another project
- session follow-up tools use `session_id` plus `project_name`
- `collect_logs` does not search log content; persisted snapshot search happens through `grep_log_snapshot`

What it returns:

- `action`
- `workspace`
- optional `session_id`
- `requested_project_names`
- `projects`
  - each project entry contains its own persisted artifact details such as
    `snapshot_dir`,
    `resolved_source_keys`, `warnings`, `retry_tips`, `collected_at`, and
    `sources`
- `sources` includes per-source deterministic metadata and status
- follow-up file reads and searches happen through the snapshot tools

Example collection call:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-filtered",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["backend"],
        "workspace":"workflow",
        "since":"30m"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Example session collection call that starts a new MCP-owned session:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-session",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["backend"],
        "workspace":"session"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

The response includes `session_id`. Reuse that returned value to add another
project into the same investigation session:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-session-batch",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage","traefik"],
        "source_keys":["backend"],
        "workspace":"session",
        "session_id":"<returned_session_id>"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Close the interactive session when the investigation is done:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"close-session",
    "method":"tools/call",
    "params":{
      "name":"close_agent_session",
      "arguments":{
        "session_id":"<returned_session_id>"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Snapshot follow-up tools:

- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `create_filtered_view`
- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`

Cleaned-view guidance:

- `collect_logs` always preserves the raw snapshot as the source of truth
- `create_filtered_view` builds a smaller deterministic cleaned view from that
  raw snapshot
- filtering works best when sources emit consistent structured logs
- the actual filtering route comes from manifest metadata:
  - `parser_type`
  - `normalization_profile`
  - `default_noise_profile`
- the manifest chooses which built-in noise profile should apply per source,
  and the filtering service applies those rules deterministically

Artifact lookup guidance:

- workflow artifact lookup is database-only; `workflow_inventory.json` is not
  written or read
- use `project_name` alone when you want the newest workflow artifact
- use `archive_name` plus `project_name` when you want to keep reading or
  grepping the same archived workflow artifact later
- use `session_id` plus `project_name` for session workspaces
- call `close_agent_session` when an interactive session investigation is done;
  this marks audit metadata only and keeps existing snapshot files readable

List files from the latest workflow artifact:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-list-snapshot",
    "method":"tools/call",
    "params":{
      "name":"list_log_snapshot_files",
      "arguments":{
        "project_name":"landingpage"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Read one saved log file:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-read-snapshot",
    "method":"tools/call",
    "params":{
      "name":"read_log_snapshot_file",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_bytes":4000
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq -r '.result.structuredContent.content'
```

Search one saved snapshot with controlled grep semantics:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-grep-snapshot",
    "method":"tools/call",
    "params":{
      "name":"grep_log_snapshot",
      "arguments":{
        "project_name":"landingpage",
        "grep":"/health",
        "source_key":"backend"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Use exactly one source selector: `source_key` for a single source, or
`source_keys` for multiple sources such as `["backend","nginx"]`.

Create one deterministic cleaned view from a saved raw artifact:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-create-filtered-view",
    "method":"tools/call",
    "params":{
      "name":"create_filtered_view",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_lines":100
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

`create_filtered_view`, `group_errors`, and `build_incident_bundle` accept
`source_key` for one source and `source_keys` for multiple sources. Do not pass
both in the same call.

Group repeated error-like findings from one saved snapshot:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-group-errors",
    "method":"tools/call",
    "params":{
      "name":"group_errors",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_groups":20
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Build one compact incident bundle from a saved snapshot:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-incident-bundle",
    "method":"tools/call",
    "params":{
      "name":"build_incident_bundle",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_groups":20
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Suggest a narrower recollection window from grouped-analysis timestamps:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-followup-window",
    "method":"tools/call",
    "params":{
      "name":"suggest_followup_window",
      "arguments":{
        "first_timestamp":"2026-04-29T10:00:00Z",
        "last_timestamp":"2026-04-29T10:05:00Z",
        "padding_minutes":5
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

### 2c. Inspect Allowed Container Paths

Use these specialist tools with the codex token when an agent needs to verify
deployed project files inside an approved container.

Important:

- these tools are not part of the workflow bootstrap inventory
- they are available to the codex token because it includes
  `container.files.read`
- `source_key` is the manifest container alias such as `backend`, `frontend`,
  `nginx`, or `traefik`
- `path` is the filesystem path inside that container

List an allowed directory:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-list",
    "method":"tools/call",
    "params":{
      "name":"list_container_directory",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "path":"/app"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Read one allowed file:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-read",
    "method":"tools/call",
    "params":{
      "name":"read_container_file",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "path":"/app/manage.py",
        "max_bytes":4000
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq -r '.result.structuredContent.content'
```

List one allowed file path:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-list-file",
    "method":"tools/call",
    "params":{
      "name":"list_container_directory",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"nginx",
        "path":"/etc/nginx/nginx.conf"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

The manifest path whitelist still applies. For example:

- `backend` and `frontend` can inspect approved `/app/...` paths
- `nginx` can inspect approved `/etc/nginx/...` paths
- `traefik` can inspect approved `/etc/traefik/...` paths

Current write layout:

- `LOGS_DIR` is the logs root
- workflow collections write under:
  - `<LOGS_DIR>/workflow/<project_key>/latest/`
  - `<LOGS_DIR>/workflow/<project_key>/archive/<archive_name>/`
- session collections write under:
  - `<LOGS_DIR>/sessions/<session_id>/<project_key>/`

### 3. List Concrete Resources

Use this to inspect directly registered resources.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"3",
    "method":"resources/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- the fixed workflow skill resources, for example:
  - `skill://workflow/project_context`
  - `skill://workflow/severity_guide`
  - `skill://workflow/bot_detection`

### 4. List Resource Templates

Use this to discover parameterized resource URIs.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"4",
    "method":"resources/templates/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- one template:
  - `skill://workflow/{skill_name}`

That template exists so invalid workflow skill reads can return structured
agent guidance instead of falling through to a generic unknown-resource error.

### 5. Read One Workflow Skill Resource

Use this when the LLM or agent decides it needs one specific skill fragment.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"5",
    "method":"resources/read",
    "params":{
      "uri":"skill://workflow/severity_guide"
    }
  }' \
  http://127.0.0.1:8001/mcp
```

What it is for:

- load only the selected skill text
- avoid sending all skill content to the LLM up front

What it returns:

- the text contents for `skill://workflow/severity_guide`

### 6. List Prompts

Use this to verify whether the server currently exposes MCP prompts.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"6",
    "method":"prompts/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- an empty `prompts` list

That is expected. The workflow currently uses `tools/call` for
`analyze_daily_log_bundle`, not `prompts/get`.

### Current Workflow Sequence

Current intended agent sequence:

1. optionally call `tools/list` to inspect the visible tool surface for the JWT
2. call `analyze_daily_log_bundle`
3. read `result.structuredContent.prompt`
4. read `result.structuredContent.mandatory_skills`
5. read `result.structuredContent.optional_skills`
6. read `result.structuredContent.tools`
7. always include the mandatory baseline in the LLM input
8. let the LLM decide whether an optional skill is needed
9. if needed, call `resources/read` for the returned skill URI
10. send the assembled prompt plus selected skill text plus deterministic data to the LLM

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

## Quality Checks

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
