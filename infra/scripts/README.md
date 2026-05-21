# Operational Scripts

This directory contains operational scripts that stay outside MCP tool internals.

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

- builds only prod-style tagged images: `prod-mcp-log-server:<TAG>`
- refuses to build with uncommitted changes unless `EMERGENCY=true`
- supports `NO_CACHE=true` when a full rebuild is required
- records the last built tag under the script state directory
- prunes older local images while keeping recent history

Local compose intentionally does not use tagged app images. For local
development, use:

```bash
docker compose up --build
```

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

- verifies the local image `prod-mcp-log-server:<TAG>` exists
- creates the production Postgres host data directory before starting `db`;
  default: `/var/lib/mcp-log-server/postgresql`, override with
  `POSTGRES_DATA_DIR`
- exposes the MCP HTTP endpoint through the existing Traefik stack at
  `https://mcp.${SITE_DOMAIN}/mcp`
- includes `docker-compose.fail2ban.yml` by default so the internal
  `fail2ban-proxy` sidecar can talk to the host fail2ban socket on the VPS
- asks for confirmation before mutating the target stack unless
  `AUTO_APPROVE=true`
- creates a database backup unless `SKIP_BACKUP=true`
- starts the database service
- applies committed migrations with `uv run migrate` unless `SKIP_MIGRATE=true`
  using the production image's `UV_NO_DEV`, `UV_FROZEN`, and `UV_NO_SYNC`
  settings so the already-built no-dev environment is reused
- starts the app service with `--force-recreate` so the selected image is rerun
- checks that the app accepts an authenticated MCP `tools/list` request and
  exposes `get_mcp_health_check`
- records the deployed tag under the script state directory after health passes

Production Postgres data is a host bind mount, not a Compose-managed Docker
volume. Normal Docker volume prune commands will not delete it. Do not point
`POSTGRES_DATA_DIR` at a temporary directory.

To prevent accidentally booting an empty production database after changing or
losing the host path, deploy and prod backup commands expect this marker file to
exist:

```text
$POSTGRES_DATA_DIR/data/pgdata/PG_VERSION
```

For a brand-new environment only, initialize deliberately with
`ALLOW_EMPTY_POSTGRES_DATA_DIR=true` and usually `SKIP_BACKUP=true`.

Dry run:

```bash
TAG=v1.2.3 DRY_RUN=true infra/scripts/release/deploy.sh
```

If the VPS should deploy without live fail2ban socket access, disable the
override explicitly:

```bash
ENABLE_FAIL2BAN_SOCKET=false TAG=v1.2.3 infra/scripts/release/deploy.sh
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
- `COMPOSE_PROJECT_NAME=mcp-log-server-prod` overrides the Docker Compose
  project name when needed.
