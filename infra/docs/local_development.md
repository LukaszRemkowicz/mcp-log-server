# Local Development

This document covers local setup, database runtime wiring, and migration
commands for `mcp-log-server`.
The root README keeps only the shortest quick-start path.


Configuration is expected to come from environment variables injected by
Doppler.

Reference variables are listed in [.env.example](../../.env.example), but the
runtime path should be Doppler rather than `env_file`.

All settings currently have development defaults in code, so the server can
start locally without explicitly setting every variable. Production startup
rejects known local placeholder secrets.

For real deployment, some values should still be treated as required.

Production-required secrets/config:

- `ENVIRONMENT`
- `MCP_HOST`
- `MCP_PORT`
- `JWT_SHARED_SECRET`
- `JWT_ISSUER`
- `JWT_AUDIENCE`
- `DATABASE_HOST`
- `DATABASE_PORT`
- `DATABASE_NAME`
- `DATABASE_USER`
- `DATABASE_PASSWORD`
- `TAG`

Production-recommended runtime config:

- `LOG_LEVEL`
- `MCP_PORT_HOST` when the host-side MCP port should differ from `8001`

Local development defaults:

- all of the above have defaults in [src/settings.py](../../src/settings.py)
- local development can run without explicitly setting every variable
- production should not rely on the built-in JWT defaults, especially
  `JWT_SHARED_SECRET=change-me-local-dev-secret`

## Database Runtime Configuration

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

## ORM Configuration

Tortoise ORM configuration lives in [src/database/config.py](../../src/database/config.py).
Database models live in [src/database/models.py](../../src/database/models.py).
Database service wrappers live under [src/database/services/](../../src/database/services).
Database startup/shutdown helpers live in [src/database/lifecycle.py](../../src/database/lifecycle.py).

The migration tool is Aerich, configured in [pyproject.toml](../../pyproject.toml)
with:

```toml
[project.scripts]
command = "cli.main:main"
makemigrations = "cli.db:makemigrations"
migrate = "cli.db:migrate"
shell = "cli.shell:main"

[tool.aerich]
tortoise_orm = "database.config.TORTOISE_ORM"
location = "./migrations"
src_folder = "./src"
```

Typer command documentation lives in
[src/cli/README.md](../../src/cli/README.md).

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
uv run command upload-project-manifest landingpage
uv run command upload-project-manifest --all
```

`src/manifests/projects` is a convenient local development location, not the
production source of truth. Production project manifests should be supplied by
the operational repository that owns them, such as the new `devops/` project,
and passed to these commands with `--path`.

Upload is create-only. Existing project manifests are reported and left
untouched. To update an existing manifest, run:

```bash
uv run command update-project-manifest --project landingpage
```

To update every existing manifest from the configured directory, run:

```bash
uv run command update-project-manifest --all
```

Run this command on the host where the Docker Compose app service is running.
Host-side `uv run command ...` bridges into the Compose app container when a
deployed tag is available, so database access uses Docker service DNS
(`db:5432`) instead of host `127.0.0.1:5432`. The command reads manifests from
`PROJECT_MANIFESTS_PATH`; Compose mounts
`${PROJECT_MANIFESTS_HOST_PATH}` at that container
path.

Production can use the short command:

```bash
uv run command update-project-manifest --all
```

Runtime MCP tools read project manifests from the database. Manifest JSON files
are source input for the upload/update commands, not runtime app settings and
not the lookup path for `collect_logs`, `list_projects`, or manifest-backed
analysis.

Manifest file source targets must be absolute paths as seen from inside the MCP
container. In production, `docker-compose.prod.yml` mounts host `/var/log` at
`/host/var/log` and host `/etc/nginx/logs` at `/host/etc/nginx/logs`. For
example, host `/var/log/app/app.jsonl` should be written in the manifest as
`/host/var/log/app/app.jsonl`. Targets are literal paths; dated filename
templates are not expanded.
