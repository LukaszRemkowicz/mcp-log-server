#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

if [[ $# -lt 1 ]]; then
    log_error "Usage: ENVIRONMENT=local|prod $0 <backup-file.dump>"
    exit 1
fi

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-local}")"
COMPOSE_FILE="${COMPOSE_FILE:-$(get_compose_file "$PROJECT_DIR" "$ENVIRONMENT")}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
BACKUP_FILE="$1"

DATABASE_NAME="${DATABASE_NAME:-mcp_log_server}"
DATABASE_USER="${DATABASE_USER:-mcp_log_server}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-local-secret}"

# docker-compose.prod.yml interpolates these even when only the db service is targeted.
export ENVIRONMENT COMPOSE_PROJECT_NAME DATABASE_NAME DATABASE_USER DATABASE_PASSWORD
export TAG="${TAG:-restore}"

if [[ ! -f "$BACKUP_FILE" ]]; then
    log_error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

container_backup="/tmp/mcp-log-server-restore.dump"

cleanup() {
    docker compose -f "$COMPOSE_FILE" exec -T db rm -f "$container_backup" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log_header "Starting $ENVIRONMENT database restore"
log_info "Compose file: $COMPOSE_FILE"
log_info "Backup file: $BACKUP_FILE"

# Step 1: require explicit confirmation before replacing the target schema.
log_step 1 7 "Confirm destructive restore"
confirm_continue "Type yes to replace the $ENVIRONMENT database schema from this backup."

# Step 2: make sure the Compose database service exists and is running.
log_step 2 7 "Ensure database service is running"
docker compose -f "$COMPOSE_FILE" up -d db

# Step 3: wait for PostgreSQL readiness before copying or restoring the dump.
log_step 3 7 "Wait for PostgreSQL readiness"
for attempt in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_isready --host=127.0.0.1 --username="$DATABASE_USER" --dbname="$DATABASE_NAME" \
        >/dev/null 2>&1; then
        break
    fi

    if [[ "$attempt" -eq 30 ]]; then
        log_error "Database did not become ready for restore."
        exit 1
    fi

    sleep 2
done

# Step 4: copy the selected backup file into the database container.
log_step 4 7 "Copy backup into database container"
docker compose -f "$COMPOSE_FILE" cp "$BACKUP_FILE" "db:$container_backup"

# Step 5: validate that the backup can be inspected by pg_restore.
log_step 5 7 "Validate backup dump"
docker compose -f "$COMPOSE_FILE" exec -T db \
    pg_restore --list "$container_backup" >/dev/null

# Step 6: reset the public schema before loading the backup contents.
log_step 6 7 "Reset public schema"
docker compose -f "$COMPOSE_FILE" exec -T db \
    env PGPASSWORD="$DATABASE_PASSWORD" \
    psql \
        --host=127.0.0.1 \
        --username="$DATABASE_USER" \
        --dbname="$DATABASE_NAME" \
        --command="DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Step 7: restore the custom-format dump into the clean public schema.
log_step 7 7 "Restore dump into target database"
docker compose -f "$COMPOSE_FILE" exec -T db \
    env PGPASSWORD="$DATABASE_PASSWORD" \
    pg_restore \
        --host=127.0.0.1 \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        --username="$DATABASE_USER" \
        --dbname="$DATABASE_NAME" \
        "$container_backup"

log_success "Restore complete."
