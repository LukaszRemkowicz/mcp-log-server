# Command-Line Helpers

This directory contains command-line helpers for local setup and maintenance.
They are exposed through the `commands` entrypoint in `pyproject.toml`.

Use these commands for project setup tasks such as generating development JWTs
or uploading project manifests. They are not MCP tools. MCP tools are called
through the FastMCP HTTP server.

Run examples from the repository root unless a command says otherwise.

## Discovery

List available commands:

```bash
uv run commands --help
```

Show one command's arguments and defaults:

```bash
uv run commands generate-dev-jwt --help
```

Command implementations live under `src/scripts/commands/`. The root command
app is `src/scripts/main.py`.

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
uv run commands generate-dev-jwt
```

Save fresh tokens for curl or local MCP checks:

```bash
uv run commands generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

Generate longer-lived development tokens, with the lifetime expressed in hours:

```bash
uv run commands generate-dev-jwt --exp-time 720
```

When `--output-file` is provided, the command writes the token JSON to that
path and does not print the tokens to the console. Parent directories are
created automatically. Without `--output-file`, the JSON is printed to stdout.

The JWTs are signed with the local development settings. Regenerate them when
they expire, when scopes change, or when identity claims change.

### Manifest Commands

Manifest commands upload or update runtime project manifest rows from JSON
files:

```bash
uv run commands upload-project-manifest --help
uv run commands update-project-manifest --help
```

These commands may run the update inside the Docker Compose app service. That
keeps manifest writes pointed at the same runtime environment as the MCP server.

`src/manifests/projects` is useful for local development examples. Production
manifests should come from the operational project that owns them, such as the
new `devops/` project, and be passed with `--path`.

`upload-project-manifest` is create-only. Existing manifest rows are left
unchanged. Use `update-project-manifest` for an existing project, or `--all`
to update every manifest JSON file from the selected path:

```bash
uv run commands update-project-manifest \
  --path src/manifests/projects \
  --project vps-security

uv run commands update-project-manifest \
  --path src/manifests/projects \
  --all
```

For file sources, write the path as the MCP container sees it. Production
Compose mounts host `/var/log` at `/host/var/log` and host `/etc/nginx/logs` at
`/host/etc/nginx/logs`. Paths are literal, so use a stable current filename or
update the manifest from the owning ops repository when a dated filename
changes.

The outer command finds the running Compose app service with:

- `COMMANDS_COMPOSE_PROJECT_NAME`
  Compose project name. Default: `mcp-log-server`.
- `COMMANDS_APP_SERVICE`
  Compose service name. Default: `app`.

For production, the Compose project is usually `mcp-log-server-prod`, so run:

```bash
COMMANDS_COMPOSE_PROJECT_NAME=mcp-log-server-prod \
COMMANDS_APP_SERVICE=app \
uv run commands update-project-manifest \
  --path src/manifests/projects \
  --all
```
