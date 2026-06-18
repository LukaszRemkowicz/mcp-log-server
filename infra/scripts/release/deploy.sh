#!/usr/bin/env bash
###############################################################################
# deploy.sh
#
# Purpose:
#   Deploy one prepared MCP log-server production image safely and repeatedly.
#
# Typical usage:
#   TAG=v0.1.0 doppler run -- infra/scripts/release/deploy.sh
#   AUTO_APPROVE=true TAG=v0.1.0 doppler run -- infra/scripts/release/deploy.sh
#
# What this script does:
#   - validates Compose configuration and required production secrets
#   - prevents concurrent deploys with a lock
#   - verifies the tagged app image exists locally
#   - verifies the tagged Docker socket app image exists locally
#   - verifies the tagged fail2ban socket app image exists locally
#   - runs a pre-deploy database backup unless SKIP_BACKUP=true
#   - applies migrations unless SKIP_MIGRATE=true
#   - recreates the socket app and MCP app containers with the selected tag
#   - waits for Docker app health before recording current_tag
#
# What this script does not do:
#   - does not build images
#   - does not create or rotate secrets
#   - does not automatically rollback after a failed health check
###############################################################################

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
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
IMAGE_NAME="${ENVIRONMENT}-mcp-log-server:${TAG}"
DOCKER_SOCKET_APP_IMAGE_NAME="${ENVIRONMENT}-mcp-docker-socket-app:${TAG}"
FAIL2BAN_SOCKET_APP_IMAGE_NAME="${ENVIRONMENT}-mcp-fail2ban-socket-app:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
LOCK_DIR="$STATE_DIR/deploy.lock"

SKIP_BACKUP="${SKIP_BACKUP:-false}"
SKIP_MIGRATE="${SKIP_MIGRATE:-false}"
DRY_RUN="${DRY_RUN:-false}"
DATABASE_NAME="${DATABASE_NAME:-mcp_log_server}"
DATABASE_USER="${DATABASE_USER:-mcp_log_server}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"
JWT_SHARED_SECRET="${JWT_SHARED_SECRET:?JWT_SHARED_SECRET is required}"
JWT_ISSUER="${JWT_ISSUER:?JWT_ISSUER is required}"
JWT_AUDIENCE="${JWT_AUDIENCE:?JWT_AUDIENCE is required}"
SITE_DOMAIN="${SITE_DOMAIN:?SITE_DOMAIN is required for Traefik MCP routing}"
DOCKER_SOCKET_GID="${DOCKER_SOCKET_GID:?DOCKER_SOCKET_GID is required}"
FAIL2BAN_LOG_GID="${FAIL2BAN_LOG_GID:?FAIL2BAN_LOG_GID is required}"
PROJECT_MANIFESTS_HOST_PATH="${PROJECT_MANIFESTS_HOST_PATH:?PROJECT_MANIFESTS_HOST_PATH is required}"
PROJECT_MANIFESTS_PATH="${PROJECT_MANIFESTS_PATH:-/app/project-manifests}"

export \
    ENVIRONMENT \
    TAG \
    COMPOSE_PROJECT_NAME \
    DATABASE_NAME \
    DATABASE_USER \
    DATABASE_PASSWORD \
    JWT_SHARED_SECRET \
    JWT_ISSUER \
    JWT_AUDIENCE \
    SITE_DOMAIN \
    DOCKER_SOCKET_GID \
    FAIL2BAN_LOG_GID \
    PROJECT_MANIFESTS_HOST_PATH \
    PROJECT_MANIFESTS_PATH

cleanup() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

deploy_step() {
    local icon="$1"
    local current="$2"
    local total="$3"
    local message="$4"

    printf "\n%s [DEPLOY] [%s/%s] %s\n" "$icon" "$current" "$total" "$message"
}

mkdir -p "$STATE_DIR"

printf "\n🚀 Deploying %s\n" "$IMAGE_NAME"
printf "⚙️  Environment: %s\n" "$ENVIRONMENT"
printf "🏷️  Release tag: %s\n" "$TAG"
printf "📦 Compose project: %s\n" "$COMPOSE_PROJECT_NAME"
printf "🧾 Compose file: %s\n" "$COMPOSE_FILE"
printf "🐳 Docker socket app image: %s\n" "$DOCKER_SOCKET_APP_IMAGE_NAME"
printf "🐳 Fail2ban socket app image: %s\n" "$FAIL2BAN_SOCKET_APP_IMAGE_NAME"
printf "🐳 Docker socket GID: %s\n" "$DOCKER_SOCKET_GID"
printf "🧾 Fail2ban log GID: %s\n" "$FAIL2BAN_LOG_GID"
printf "📁 Project manifests host path: %s\n" "$PROJECT_MANIFESTS_HOST_PATH"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")
printf "📁 State directory: %s\n" "$STATE_DIR"

wait_for_app_container_health() {
    local container_id
    local health_status

    for attempt in {1..30}; do
        container_id="$(docker compose "${COMPOSE_ARGS[@]}" ps -q app)"
        if [[ -n "$container_id" ]]; then
            health_status="$(
                docker inspect \
                    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
                    "$container_id" \
                    2>/dev/null \
                    || true
            )"
            if [[ "$health_status" == "healthy" ]]; then
                printf "✅ Docker app healthcheck passed\n"
                return 0
            fi
            printf "⏳ Waiting for Docker app health (%s/30): %s\n" "$attempt" "$health_status"
        else
            printf "⏳ Waiting for Docker app container (%s/30)\n" "$attempt"
        fi
        sleep 2
    done

    log_error "Docker app healthcheck did not pass."
    docker compose "${COMPOSE_ARGS[@]}" ps
    return 1
}

# Step 1: take a deploy lock so two deploys cannot mutate the stack at once.
deploy_step "🔒" 1 9 "Acquire deploy lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log_error "Another deployment is already running: $LOCK_DIR"
    exit 1
fi
printf "✅ Deploy lock acquired: %s\n" "$LOCK_DIR"

# Step 2: for dry runs, validate Compose interpolation and exit before changes.
deploy_step "🧪" 2 9 "Check dry-run mode and validate Compose config"
if [[ "$DRY_RUN" == "true" ]]; then
    printf "🧾 DRY RUN: validating Compose config only\n"
    docker compose "${COMPOSE_ARGS[@]}" config >/dev/null
    printf "✅ Dry run complete\n"
    exit 0
fi
printf "✅ Compose config validation will run during deploy with required variables set\n"

# Step 3: verify the image was built or pulled before starting deployment.
deploy_step "🔍" 3 9 "Verify release images exist"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
if ! docker image inspect "$DOCKER_SOCKET_APP_IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $DOCKER_SOCKET_APP_IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
if ! docker image inspect "$FAIL2BAN_SOCKET_APP_IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $FAIL2BAN_SOCKET_APP_IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
printf "✅ Release image found: %s\n" "$IMAGE_NAME"
printf "✅ Release image found: %s\n" "$DOCKER_SOCKET_APP_IMAGE_NAME"
printf "✅ Release image found: %s\n" "$FAIL2BAN_SOCKET_APP_IMAGE_NAME"

deploy_step "⚠️" 4 9 "Confirm deploy"
confirm_continue "Type yes to deploy $IMAGE_NAME to $ENVIRONMENT."
printf "✅ Deploy confirmed\n"

# Step 5: back up the target database before changing containers or schema.
deploy_step "💾" 5 9 "Run pre-deploy database backup"
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
deploy_step "🐘" 6 9 "Ensure database service is running"
docker compose "${COMPOSE_ARGS[@]}" up -d db
printf "✅ Database service is running or starting\n"

# Step 7: apply committed migrations from the release image.
deploy_step "🧬" 7 9 "Apply database migrations"
if [[ "$SKIP_MIGRATE" != "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" run --rm app uv run migrate
    printf "✅ Database migrations applied\n"
else
    log_warn "Skipping database migrations because SKIP_MIGRATE=true"
    confirm_continue "Type yes to continue without applying migrations."
fi

# Step 8: start or update the Docker-backed application containers with the selected tag.
deploy_step "🚀" 8 9 "Start application containers"
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans docker-socket-app
printf "✅ Docker socket app container recreated\n"
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans fail2ban-socket-app
printf "✅ Fail2ban socket app container recreated\n"
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate --remove-orphans app
printf "✅ Application container recreated\n"

# Step 9: wait for Docker to confirm the app accepts unauthenticated liveness probes.
deploy_step "🩺" 9 9 "Wait for Docker app health"
wait_for_app_container_health
printf "%s\n" "$TAG" > "$STATE_DIR/current_tag"
printf "🎉 Deploy complete: %s\n" "$IMAGE_NAME"
