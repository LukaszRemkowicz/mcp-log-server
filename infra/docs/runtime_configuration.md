# Runtime Configuration

This document describes auth, logging, MCP transport, Docker socket, and
production compose runtime settings.


## Auth Configuration

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

The concrete caller authorization model is resolved through `CALLER_AUTH`, which
defaults to `database.models.McpCaller`.

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

The local JWT signing algorithm is fixed in [src/settings.py](../../src/settings.py)
as `HS256`.

- `JWT_SHARED_SECRET`
  Shared secret used to sign and verify local example JWTs.
  Default: `change-me-local-dev-secret`

- `JWT_ISSUER`
  Required `iss` claim for local example JWTs. This is not a secret; it is a
  public token issuer identifier that the server validates.
  Default: `mcp-log-server-dev`

- `JWT_AUDIENCE`
  Required `aud` claim for local example JWTs. This is not a secret; it is the
  public audience identifier expected by the server.
  Default: `mcp-log-server`

The local example JWT lifetime is fixed in [src/settings.py](../../src/settings.py)
as `86400` seconds.

Generate example JWTs locally:

```bash
uv run command generate-dev-jwt
```

That prints a JSON payload with:

- `workflow_agent`
- `codex_agent`
- `created_at`
- `updated_at`

The command reads caller claims from `mcp_callers` when rows exist. If caller
rows are missing, it uses built-in default claims without creating database
rows.

The usual local flow is to save it into `.agent/DEV_JWT_TOKENS.json`:

```bash
uv run command generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

When `--output-file` is provided, the command writes the token JSON to that
path instead of printing tokens to the console. Parent directories are created
automatically. Without `--output-file`, the JSON is printed to stdout.

The command also accepts explicit identity claim overrides when you need
tokens for a different local caller:

```bash
uv run command generate-dev-jwt \
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

## Logging Configuration

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

The project log output format is fixed to JSON in [src/settings.py](../../src/settings.py),
so every project log line is emitted as one JSON object.

Current project logs include:

- startup of the FastMCP HTTP service
- MCP tool registration
- workflow tool calls such as:
  - `analyze_daily_log_bundle`
  - `get_mcp_service_status`
  - `get_mcp_health_check`

Example:

```bash
LOG_LEVEL=DEBUG doppler run -- docker compose up --build
```

## MCP Configuration

These settings control how the local FastMCP HTTP server starts.

Manifests and logs are intentionally separate:

- manifest JSON paths are passed to `uv run command upload-project-manifest`
  and `uv run command update-project-manifest` with `--path`
- this repository may use `src/manifests/projects` for local manifest examples,
  but production manifests should be provided by the operational repository
  that owns them, such as `devops/`
- runtime MCP tools read persisted manifest rows from the database
- file-backed manifest source targets must be absolute paths, so each source
  declares exactly where its log file lives
- in production Compose, host `/var/log` is visible inside MCP as
  `/host/var/log`, and host `/etc/nginx/logs` is visible as
  `/host/etc/nginx/logs`
- manifest file targets are literal paths; dated filename templates are not
  expanded by MCP

- `MCP_HOST`
  Host address the FastMCP service binds inside the running process.
  Default: `127.0.0.1`

  Docker Compose injects `0.0.0.0` inside app containers so the service is
  reachable through the loopback-only host port binding.

- `MCP_PORT`
  Port the FastMCP service binds inside the running process.
  Default: `8001`

- `MCP_PATH`
  HTTP path where the FastMCP endpoint is exposed.
  Default: `/mcp`

  MCP JSON-RPC requests go to:

  - `http://127.0.0.1:8001/mcp`

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

The local `app` service mounts `/var/run/docker.sock` so MCP collection and
inspection tools can read approved Docker metadata and logs. The app process
still runs as the non-root `app` user (`uid=999`); Compose only adds the
container process to the Docker socket's group through `DOCKER_SOCKET_GID`.

On Linux hosts where `/var/run/docker.sock` is not group-readable by group `0`,
discover the socket group id with:

```bash
stat -c '%g' /var/run/docker.sock
```

Then pass it into Compose:

```bash
DOCKER_SOCKET_GID="$(stat -c '%g' /var/run/docker.sock)" \
  doppler run -- docker compose up --build
```

To see the group name as well:

```bash
getent group "$(stat -c '%g' /var/run/docker.sock)"
```

For a production-like container run without bind mounts or file watching, use
the dedicated production compose file:

```bash
doppler run -- docker compose -f docker-compose.prod.yml up --build -d
```

Production Postgres data is stored in the Compose-managed `postgres-data`
Docker volume. The release scripts do not require a host data directory or
`POSTGRES_DATA_DIR` override. Keep database backups current before Docker volume
cleanup or host maintenance.

The production deploy script includes the fail2ban socket override by default,
so the normal VPS path is:

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
- stores Postgres data in the Compose-managed `postgres-data` Docker volume
- builds the Dockerfile `production` stage with
  `uv sync --frozen --no-dev --compile-bytecode`
- sets `UV_NO_DEV=1`, `UV_FROZEN=1`, and `UV_NO_SYNC=1` inside the production
  image so `uv run` commands use the already-built project environment instead
  of installing or resyncing packages at runtime
- starts the server with `uv run python -m main`

The app container exposes the MCP HTTP endpoint on port `8001`:

- `POST /mcp`

Example manual requests live in
[src/tests/requests.http](../../src/tests/requests.http).

To inspect the structured workflow bootstrap once the container is up:

```bash
curl -fsS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"tools/call","params":{"name":"analyze_daily_log_bundle","arguments":{}}}' \
  http://127.0.0.1:8001/mcp
```
