# Typer Commands

This directory contains the project Typer command app exposed through the
`commands` entrypoint in `pyproject.toml`.

Use it for local developer operations that belong to the MCP log server itself,
not for MCP tool calls. MCP tools are served by FastMCP; Typer commands are
host-side maintenance and setup helpers.

## Discovery

List available commands:

```bash
uv run commands --help
```

Show one command's arguments and defaults:

```bash
uv run commands generate-dev-jwt --help
```

Command implementations live under `src/scripts/commands/`. The root Typer app
is `src/scripts/main.py`.

## Commands

### `generate-dev-jwt`

Generate signed local development JWTs for MCP clients.

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
they expire, when scopes change, or when the expected identity claims change.

### Manifest Commands

Manifest commands upload or update runtime project manifest rows from JSON
files:

```bash
uv run commands upload-project-manifest --help
uv run commands update-project-manifest --help
```

These commands may proxy work into the Docker Compose app service so they run
against the same runtime environment as the MCP server.

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
