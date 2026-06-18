# Operational Scripts

This directory contains operational scripts for backups, restores, releases,
deploys, and production log access. These scripts are for operators and
maintainers; they are separate from MCP tools.

Run examples from the repository root.

Supported environments:

- `local`
- `prod`

There is no staging environment in this repository.

## Database Backups

Create a database backup:

```bash
ENVIRONMENT=local infra/scripts/db_backup/backup_db.sh
ENVIRONMENT=prod TAG=v1.2.3 infra/scripts/db_backup/backup_db.sh
```

Backups are PostgreSQL custom-format dumps created with:

- `pg_dump --format=custom`
- `--no-owner`
- `--no-privileges`

Default storage location:

- `/var/backups/mcp-log-server/<environment>` when that parent directory is
  writable or the script runs as root
- `.agent/backups/db/<environment>` as a local developer fallback

Override with:

```bash
BACKUP_DIR=/secure/backups/mcp-log-server/prod \
ENVIRONMENT=prod \
TAG=v1.2.3 \
infra/scripts/db_backup/backup_db.sh
```

Backup filenames use UTC timestamps:

```text
mcp_log_server_<environment>_<YYYYMMDDTHHMMSSZ>.dump
```

Default retention is 14 days. Override with:

```bash
RETENTION_DAYS=30 ENVIRONMENT=prod TAG=v1.2.3 infra/scripts/db_backup/backup_db.sh
```

## Database Restore

Restore replaces the target database `public` schema from one backup file.

Local restore:

```bash
ENVIRONMENT=local infra/scripts/db_backup/restore_db.sh .agent/backups/db/local/mcp_log_server_local_20260508T210000Z.dump
```

Prod restore:

```bash
ENVIRONMENT=prod TAG=v1.2.3 infra/scripts/db_backup/restore_db.sh /var/backups/mcp-log-server/prod/mcp_log_server_prod_20260508T210000Z.dump
```

The restore script asks for `yes` before replacing the target schema. Automation
can set `AUTO_APPROVE=true` only when the caller already performed the required
human approval elsewhere.

Restore expectations:

- restore into the same major Postgres version used by the compose file
- restore only from trusted backup files
- keep the backup file available until the app has been verified after restore
- run restore during a maintenance window for prod

## Safe Local Reset

Local reset is not a prod restore policy. For local development, it is safe to
remove and recreate the local compose volume when no local data matters:

```bash
docker compose down
docker volume rm mcp-log-server_postgres-data
docker compose up -d db
uv run makemigrations
```

Use the restore script when you need a specific backup state. Do not use local
volume reset instructions on prod.

## Build

Build the tagged prod app image:

```bash
TAG=v1.2.3 infra/scripts/release/build.sh
```

If `TAG` is omitted, the script uses the exact Git tag checked out in the
working tree.

Build behavior:

- builds only prod-style tagged images:
  `prod-mcp-log-server:<TAG>`, `prod-mcp-docker-socket-app:<TAG>`, and
  `prod-mcp-fail2ban-socket-app:<TAG>`
- refuses to build with uncommitted changes unless `EMERGENCY=true` or
  `--emergency` is passed
- supports `NO_CACHE=true` when a full rebuild is required
- records the last built tag under the script state directory
- prunes older local images, keeping only the built tag

Local compose intentionally does not use tagged app images. For local
development, use:

```bash
docker compose up --build
```

## Release

Build and deploy one tagged prod release:

```bash
TAG=v1.2.3 infra/scripts/release/release.sh
```

For non-interactive automation, pass approval explicitly:

```bash
AUTO_APPROVE=true TAG=v1.2.3 infra/scripts/release/release.sh
```

For an emergency release from a dirty working tree, pass the emergency flag:

```bash
TAG=v1.2.3 infra/scripts/release/release.sh --emergency
```

Release behavior:

- validates the same prod environment and tag as the lower-level scripts
- runs `release/build.sh`
- runs `release/deploy.sh`
- keeps backup, migration, confirmation, and health-check behavior inside
  `deploy.sh`

## Deploy

Deploy an already-built prod image:

```bash
TAG=v1.2.3 infra/scripts/release/deploy.sh
```

If `TAG` is omitted, the script uses the exact Git tag checked out in the
working tree.

For non-interactive automation, pass approval explicitly:

```bash
AUTO_APPROVE=true TAG=v1.2.3 infra/scripts/release/deploy.sh
```

Deploy behavior:

- verifies the local MCP app, Docker socket app, and fail2ban socket app images
  exist for the selected tag
- exposes the MCP HTTP endpoint through the existing Traefik stack at
  `https://mcp.${SITE_DOMAIN}/mcp`
- starts the internal Docker and fail2ban Unix-socket app containers before the
  MCP app, so MCP does not mount privileged host sockets directly
- asks for confirmation before mutating the target stack unless
  `AUTO_APPROVE=true`
- creates a database backup unless `SKIP_BACKUP=true`
- starts the database service
- applies committed migrations with `uv run migrate` unless `SKIP_MIGRATE=true`
  using the production image's `UV_NO_DEV`, `UV_FROZEN`, and `UV_NO_SYNC`
  settings so the already-built no-dev environment is reused
- starts the socket app services and MCP app service with `--force-recreate`
  and `--remove-orphans` so the selected images are rerun and removed Compose
  services are cleaned up
- waits for Docker to mark the app service healthy through the unauthenticated
  `/healthz` liveness endpoint
- records the deployed tag under `/var/lib/mcp-log-server/prod/current_tag`
  after health passes

After deploy records `current_tag`, host-side `uv run shell` and
`uv run command ...` helpers use that tag as the default `TAG` when the caller
does not provide one. Set `TAG=vX.Y.Z` explicitly to run a host-side command
against a specific production image before or outside the recorded deployment
state.

Production Postgres data is stored in the Compose-managed `postgres-data`
Docker volume. Keep database backups current before Docker volume cleanup or
host maintenance.

Dry run:

```bash
TAG=v1.2.3 DRY_RUN=true infra/scripts/release/deploy.sh
```

## Production logs

Read production container logs without loading `docker-compose.prod.yml` and
without requiring Doppler or Compose-time variables:

```bash
infra/scripts/logs.sh app
```

Follow logs:

```bash
FOLLOW=true infra/scripts/logs.sh app
```

Read another service:

```bash
infra/scripts/logs.sh db
```

Useful options:

- `TAIL_LINES=500` changes the number of lines shown.
- `COMPOSE_PROJECT_NAME=mcp` overrides the Docker Compose
  project name when needed.
