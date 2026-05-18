#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../utils.sh
source "$SCRIPT_DIR/../utils.sh"

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-prod}")"
validate_release_environment "$ENVIRONMENT"

TAG="${TAG:-$(git -C "$PROJECT_DIR" describe --tags --exact-match 2>/dev/null || true)}"
validate_tag "$TAG"

COMPOSE_FILE="${COMPOSE_FILE:-$(get_compose_file "$PROJECT_DIR" "$ENVIRONMENT")}"
FAIL2BAN_COMPOSE_FILE="${FAIL2BAN_COMPOSE_FILE:-$PROJECT_DIR/docker-compose.fail2ban.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
IMAGE_NAME="${ENVIRONMENT}-mcp-log-server:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
LOCK_DIR="$STATE_DIR/deploy.lock"

SKIP_BACKUP="${SKIP_BACKUP:-false}"
SKIP_MIGRATE="${SKIP_MIGRATE:-false}"
DRY_RUN="${DRY_RUN:-false}"
ENABLE_FAIL2BAN_SOCKET="${ENABLE_FAIL2BAN_SOCKET:-true}"

DATABASE_NAME="${DATABASE_NAME:-mcp_log_server}"
DATABASE_USER="${DATABASE_USER:-mcp_log_server}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-local-secret}"
SITE_DOMAIN="${SITE_DOMAIN:?SITE_DOMAIN is required for Traefik MCP routing}"

export ENVIRONMENT TAG COMPOSE_PROJECT_NAME DATABASE_NAME DATABASE_USER DATABASE_PASSWORD SITE_DOMAIN

cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$STATE_DIR"

log_header "Deploying $IMAGE_NAME"
log_info "Compose file: $COMPOSE_FILE"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$ENABLE_FAIL2BAN_SOCKET" == "true" ]]; then
    if [[ ! -f "$FAIL2BAN_COMPOSE_FILE" ]]; then
        log_error "Fail2ban Compose override not found: $FAIL2BAN_COMPOSE_FILE"
        exit 1
    fi
    COMPOSE_ARGS+=(-f "$FAIL2BAN_COMPOSE_FILE")
    log_info "Fail2ban socket override: $FAIL2BAN_COMPOSE_FILE"
else
    log_warn "Fail2ban socket override disabled because ENABLE_FAIL2BAN_SOCKET=false"
fi
log_info "State directory: $STATE_DIR"

# Step 1: take a deploy lock so two deploys cannot mutate the stack at once.
log_step 1 9 "Acquire deploy lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log_error "Another deployment is already running: $LOCK_DIR"
    exit 1
fi

# Step 2: for dry runs, validate Compose interpolation and exit before changes.
log_step 2 9 "Check dry-run mode and validate Compose config"
if [[ "$DRY_RUN" == "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
    log_success "Dry run complete."
    exit 0
fi

# Step 3: verify the image was built or pulled before starting deployment.
log_step 3 9 "Verify release image exists"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi

log_step 4 9 "Confirm deploy"
confirm_continue "Type yes to deploy $IMAGE_NAME to $ENVIRONMENT."

# Step 5: back up the target database before changing containers or schema.
log_step 5 9 "Run pre-deploy database backup"
if [[ "$SKIP_BACKUP" != "true" ]]; then
    ENVIRONMENT="$ENVIRONMENT" \
    TAG="$TAG" \
    COMPOSE_FILE="$COMPOSE_FILE" \
    COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" \
    DATABASE_NAME="$DATABASE_NAME" \
    DATABASE_USER="$DATABASE_USER" \
    DATABASE_PASSWORD="$DATABASE_PASSWORD" \
        "$PROJECT_DIR/infra/scripts/db_backup/backup_db.sh"
else
    log_warn "Skipping database backup because SKIP_BACKUP=true"
    confirm_continue "Type yes to continue without a pre-deploy backup."
fi

# Step 6: make sure the database service is running before migrations.
log_step 6 9 "Ensure database service is running"
docker compose "${COMPOSE_ARGS[@]}" up -d db

# Step 7: apply committed migrations from the release image.
log_step 7 9 "Apply database migrations"
if [[ "$SKIP_MIGRATE" != "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" run --rm app uv run migrate
else
    log_warn "Skipping database migrations because SKIP_MIGRATE=true"
    confirm_continue "Type yes to continue without applying migrations."
fi

# Step 8: start or update the application container with the selected image.
log_step 8 9 "Start application container"
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate app

# Step 9: verify the app process accepts local TCP connections.
log_step 9 9 "Verify application TCP health"
for attempt in {1..30}; do
    if docker compose "${COMPOSE_ARGS[@]}" exec -T app \
        python -c 'import socket; socket.create_connection(("127.0.0.1", 8001), 5).close()' \
        >/dev/null 2>&1; then
        # Step 9: record the currently deployed tag after the health check passes.
        printf "%s\n" "$TAG" > "$STATE_DIR/current_tag"
        log_success "Deploy complete: $IMAGE_NAME"
        exit 0
    fi

    log_info "Waiting for app to accept connections ($attempt/30)"
    sleep 2
done

log_error "Deployment failed health check."
docker compose "${COMPOSE_ARGS[@]}" ps
exit 1
