# Operations Runbook Overview

This page is a short routing note for operational commands. The detailed
runbook lives in [infra/scripts/README.md](../scripts/README.md).


Operational scripts live in [infra/scripts/](../scripts/README.md).
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
checks that the app accepts an authenticated MCP `tools/list` request exposing
`get_mcp_health_check`. It asks for confirmation before mutating the target
stack unless `AUTO_APPROVE=true`, and starts the app with `--force-recreate` so
the selected image is rerun. Use `SKIP_BACKUP=true` or `SKIP_MIGRATE=true` only
for an intentional operator-controlled run.
