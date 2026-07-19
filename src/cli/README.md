# Command-Line Helpers

This directory contains command-line helpers for local setup and maintenance.
They are exposed through the `command` entrypoint in `pyproject.toml`.

Use these commands for project setup tasks such as generating development JWTs
or uploading project manifests. They are not MCP tools. MCP tools are called
through the FastMCP HTTP server.

Run examples from the repository root unless a command says otherwise.

## Discovery

List available commands:

```bash
uv run command --help
```

Show one command's arguments and defaults:

```bash
uv run command generate-dev-jwt --help
```

Command implementations live under `src/cli/commands/`. The root command app
is `src/cli/main.py`.

## Commands

### `generate-dev-jwt`

Generate signed local development JWTs for MCP clients. These tokens are for
local development and manual curl checks only.

The command prints JSON containing:

- `workflow_agent`
- `codex_agent`
- `created_at`
- `updated_at`

Default usage:

```bash
uv run command generate-dev-jwt
```

Save fresh tokens for curl or local MCP checks:

```bash
uv run command generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

Generate longer-lived development tokens, with the lifetime expressed in hours:

```bash
uv run command generate-dev-jwt --exp-time 720
```

When `--output-file` is provided, the command writes the token JSON to that
path and does not print the tokens to the console. Parent directories are
created automatically. Without `--output-file`, the JSON is printed to stdout.

The JWTs are signed with the local development settings. Regenerate them when
they expire, when scopes change, or when identity claims change.

The command reads caller claims from `mcp_callers` when rows exist. If caller
rows are missing, it uses built-in default claims without creating database
rows.

### `slow-analysis-calls`

Review slow snapshot-analysis MCP calls from existing audit metadata. This is
an operator command, not an MCP tool, and is meant for production maintainers
debugging expensive snapshot-analysis paths.

Default usage:

```bash
uv run command slow-analysis-calls
```

Useful filters:

```bash
uv run command slow-analysis-calls \
  --min-duration 2 \
  --tool-name inspect_proxy_activity \
  --project-name landingpage \
  --limit 20
```

The output lists tool name, duration, caller workspace, project, requested
source keys, sanitized selector arguments, and best-effort collected source line
counts matched from `collect_logs` metadata. When no matching snapshot metadata
can be identified, the timing row is still printed with line-count context
marked unavailable.

### Manifest Commands

Manifest commands upload or update runtime project manifest rows from JSON
files:

```bash
uv run command upload-project-manifest --help
uv run command update-project-manifest --help
```

These commands may run the update inside the Docker Compose app service. That
keeps manifest writes pointed at the same runtime environment as the MCP server.

`src/manifests/projects` is useful for local development examples. Production
manifests should come from the operational project that owns them, such as the
new `devops/` project. Configure `PROJECT_MANIFESTS_HOST_PATH` with that host
directory; Compose mounts it at `PROJECT_MANIFESTS_PATH` inside the app
container.

`upload-project-manifest` is create-only. Existing manifest rows are left
unchanged. Use `update-project-manifest` for an existing project, or `--all`
to update every manifest JSON file from the selected path:

```bash
uv run command update-project-manifest \
  --project vps-security

uv run command update-project-manifest \
  --all
```

For file sources, write the path as the MCP container sees it. Production
Compose mounts host `/var/log` at `/host/var/log`. Paths are literal, so use a
stable current filename or update the manifest from the owning ops repository
when a dated filename changes.

The command defaults to `PROJECT_MANIFESTS_PATH`. Local and production Compose
mount `${PROJECT_MANIFESTS_HOST_PATH}` there, so the
host command can stay short while the Typer code reads ordinary JSON files from
inside the app container.

Host-side commands use `settings.ENVIRONMENT` to choose the Compose file:

- `dev`, `development`, or `local` use `docker-compose.yml`
- `prod` or `production` use `docker-compose.prod.yml`

The common case stays short:

```bash
uv run command update-project-manifest \
  --all
```

Preview the Docker Compose command without running it:

```bash
uv run command --dry-run generate-dev-jwt
```

Production commands read the deployed tag from
`/var/lib/mcp-log-server/prod/current_tag` and pass it into Docker Compose for
image selection. Set `TAG=vX.Y.Z` explicitly to run a command against a
specific production image before or outside the recorded deployment state.
