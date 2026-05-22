#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-local}")"
COMPOSE_FILE="${COMPOSE_FILE:-$(get_compose_file "$PROJECT_DIR" "$ENVIRONMENT")}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
BACKUP_DIR="$(get_backup_dir "$PROJECT_DIR" "$ENVIRONMENT")"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

DATABASE_NAME="${DATABASE_NAME:-mcp_log_server}"
DATABASE_USER="${DATABASE_USER:-mcp_log_server}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-local-secret}"

# docker-compose.prod.yml interpolates these even when only the db service is targeted.
export ENVIRONMENT COMPOSE_PROJECT_NAME DATABASE_NAME DATABASE_USER DATABASE_PASSWORD
export TAG="${TAG:-backup}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/mcp_log_server_${ENVIRONMENT}_${timestamp}.dump"
tmp_file="$backup_file.tmp"
lock_dir="$BACKUP_DIR/.backup.lock"

cleanup() {
    rm -f "$tmp_file"
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"

# Step 1: take a lock so two backup processes cannot write the same target area.
log_step 1 8 "Acquire backup lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
    log_error "Another backup is already running: $lock_dir"
    exit 1
fi

log_header "Starting $ENVIRONMENT database backup"
log_info "Backup file: $backup_file"

# Step 2: make sure the Compose database service exists and is running.
log_step 2 8 "Ensure database service is running"
docker compose -f "$COMPOSE_FILE" up -d db

# Step 3: wait for PostgreSQL readiness before running pg_dump.
log_step 3 8 "Wait for PostgreSQL readiness"
for attempt in {1..30}; do
    if docker compose -f "$COMPOSE_FILE" exec -T db \
        pg_isready --host=127.0.0.1 --username="$DATABASE_USER" --dbname="$DATABASE_NAME" \
        >/dev/null 2>&1; then
        break
    fi

    if [[ "$attempt" -eq 30 ]]; then
        log_error "Database did not become ready for backup."
        exit 1
    fi

    sleep 2
done

# Step 4: create a custom-format dump with ownership and privilege metadata removed.
log_step 4 8 "Create custom-format database dump"
docker compose -f "$COMPOSE_FILE" exec -T db \
    env PGPASSWORD="$DATABASE_PASSWORD" \
    pg_dump \
        --host=127.0.0.1 \
        --format=custom \
        --no-owner \
        --no-privileges \
        --username="$DATABASE_USER" \
        "$DATABASE_NAME" \
    > "$tmp_file"

# Step 5: reject empty dump files before they can be promoted as backups.
log_step 5 8 "Verify backup file is not empty"
if [[ ! -s "$tmp_file" ]]; then
    log_error "Backup file is empty."
    exit 1
fi

# Step 6: validate the dump using the pg_restore version from the DB container.
log_step 6 8 "Validate backup dump"
docker compose -f "$COMPOSE_FILE" exec -T db pg_restore --list < "$tmp_file" >/dev/null

# Step 7: promote the validated temporary dump to its final timestamped path.
log_step 7 8 "Promote validated backup"
mv "$tmp_file" "$backup_file"

# Step 8: prune old backups for this environment according to RETENTION_DAYS.
log_step 8 8 "Prune backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" \
    -type f \
    -name "mcp_log_server_${ENVIRONMENT}_*.dump" \
    -mtime "+$RETENTION_DAYS" \
    -delete

log_success "Backup complete: $backup_file"
