# mcp-log-server

Dedicated FastMCP service for deterministic log collection, filtering, and VPS
inspection.

This repository is the implementation home for the MCP server described in
[infra/docs/current_project_state.md](infra/docs/current_project_state.md).

## Current Status

Current repository foundation:

- Python application structure under `src/`
- minimal settings and Docker-first local bootstrap
- architecture docs and repository setup docs
- FastMCP tool/resource workflow bootstrap
- JWT-protected HTTP integration tests and in-memory FastMCP client tests

This repo does not yet implement real log collection parity with the existing
collector.

The repository now includes a sample source manifest at
`src/manifests/landingpage.json`. This manifest is the project
inventory/config
that later collection tools will consume after authorization selects the
project/resources.

The repository also now includes a copied MCP-owned monitoring asset bundle
under `src/agent_assets/`.

Current MCP workflow surface includes:

- tools: `analyze_daily_log_bundle`, `collect_logs`, `list_projects`, `get_mcp_service_status`, `get_mcp_health_check`, `read_container_file`, `stat_container_path`, `list_container_directory`
- resources: concrete workflow skill resources such as
  `skill://workflow/project_context`, `skill://workflow/severity_guide`,
  `skill://workflow/bot_detection`
- prompts: none exposed right now

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
  NEW/
  repository_foundation.md
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
- `MANIFEST_PATH`
- `MCP_PATH`
- `MCP_STATELESS_HTTP`
- `MCP_JSON_RESPONSE`

Production-recommended runtime config:

- `LOG_LEVEL`
- `LOG_FORMAT`
- `JWT_ALGORITHM`
- `JWT_EXPIRATION_SECONDS`

Local development defaults:

- all of the above have defaults in [src/settings.py](/Users/lukaszremkowicz/Projects/mcp-log-server/src/settings.py:1)
- local development can run without explicitly setting every variable
- production should not rely on the built-in JWT defaults, especially
  `JWT_SHARED_SECRET=change-me-local-dev-secret`

### Auth Configuration

The server now uses FastMCP's HTTP auth layer, so tool visibility and tool
calls are evaluated per bearer token, not once at process startup.

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
uv run python infra/scripts/generate_dev_jwt.py
```

That prints a JSON payload with:

- `workflow_agent`
- `codex_agent`
- `created_at`
- `updated_at`

The usual local flow is to save it into `.agent/DEV_JWT_TOKENS.json`:

```bash
uv run python infra/scripts/generate_dev_jwt.py > .agent/DEV_JWT_TOKENS.json
```

Then export the values you want to use with `curl`:

```bash
export WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' .agent/DEV_JWT_TOKENS.json)"
export CODEX_AGENT_JWT="$(jq -r '.codex_agent' .agent/DEV_JWT_TOKENS.json)"
```

Current example JWT capabilities:

- `workflow_agent`
  - `collect_logs`
  - `list_projects`
  - `analyze_daily_log_bundle`
  - `get_mcp_service_status`
  - `get_mcp_health_check`
  - `resources/read` for `skill://workflow/{skill_name}`

- `codex_agent`
  - `collect_logs`
  - `list_projects`
  - `get_mcp_service_status`
  - `get_mcp_health_check`
  - `read_container_file`
  - `stat_container_path`
  - `list_container_directory`

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

- `MANIFEST_PATH`
  Path to the project source manifest file.
  Default: `src/manifests/landingpage.json`

  This is resolved relative to the repository root, so:

  - `MANIFEST_PATH=src/manifests/landingpage.json`
    resolves to `/app/src/manifests/landingpage.json` in Docker
  - an absolute path is also allowed

  The manifest is the project inventory/config that later collector-style
  tools will use to know what sources exist for the selected project.

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

The `app` service mounts `./src` into the container and reloads automatically
when files under `src/` change, including copied workflow assets such as
prompts, skills, schemas, and examples.

For a production-like container run without bind mounts or file watching, use
the dedicated production compose file:

```bash
doppler run -- docker compose -f docker-compose.prod.yml up --build -d
```

Production compose differences:

- runs only the `app` service
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
uv run python infra/scripts/generate_dev_jwt.py > .agent/DEV_JWT_TOKENS.json
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

- `collect_logs`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `analyze_daily_log_bundle`

What it returns right now for the codex token:

- `collect_logs`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `read_container_file`
- `stat_container_path`
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
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
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
- `manifest_file`
- `source_keys`
- `source_types`
- `file_sources_available`
- `docker_sources_available`

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
        "project_name":"landingpage",
        "source_keys":["nginx","backend"],
        "save_to_files":false,
        "tail_lines":200,
        "timestamps":true,
        "since":"30m"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

What it is for:

- first deterministic collection surface after the workflow skeleton
- explicit project, source, and docker/file option tracking
- manifest-driven source resolution before later snapshot/filtering work
- project-scoped persistence under the configured logs root

Agent-facing collect_logs arguments:

- `project_name`
- `source_keys`
- `save_to_files`
- optional `tail_lines`
- `timestamps`
- `since`
- `until`

Important:

- if `tail_lines` is omitted, the server requests full source output where supported
- agents should prefer setting `tail_lines` when they do not need the full history
- when an unbounded source is too slow or too large, the response includes retry guidance pointing back to `tail_lines`

What it returns:

- `action`
- `requested_project_name`
- `authorized_project_name`
- `effective_project_name`
- `requested_source_keys`
- `requested_tail_lines`
- `effective_tail_lines`
- `requested_timestamps`
- `requested_since`
- `requested_until`
- `unknown_requested_source_keys`
- `resolved_source_keys`
- `logs_by_source`
- `warnings`
- `retry_tips`
- `project_output_dir`
- `latest_output_dir`
- `archive_dir`
- `sources`

Important response detail:

- `logs_by_source` is the agent-first field for the actual collected log text
- `sources` still includes the per-source deterministic metadata and status

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

Stat one allowed path:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-stat",
    "method":"tools/call",
    "params":{
      "name":"stat_container_path",
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

- `DOCKER_LOGS_DIR` is the logs root
- each project writes under:
  - `<DOCKER_LOGS_DIR>/<project_key>/latest/`
- the previous `latest` snapshot is moved into:
  - `<DOCKER_LOGS_DIR>/<project_key>/archive/<timestamp>/`

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
- shared `python-tests-uv` workflow running `uv run pytest`
  - covers unit-style FastMCP client tests
  - covers JWT-protected HTTP integration tests
- Docker Compose validation
- Docker image build check
- CodeQL analysis on pull requests and the weekly schedule
- VERSION bump validation on `dev -> main` pull requests
- tag creation from `VERSION` on pushes to `main`
