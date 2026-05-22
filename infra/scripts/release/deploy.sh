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
#   - verifies the tagged image exists locally
#   - runs a pre-deploy database backup unless SKIP_BACKUP=true
#   - applies migrations unless SKIP_MIGRATE=true
#   - recreates the app container with the selected image
#   - verifies authenticated MCP health before recording current_tag
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
DATABASE_PASSWORD="${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"
JWT_SHARED_SECRET="${JWT_SHARED_SECRET:?JWT_SHARED_SECRET is required}"
JWT_ISSUER="${JWT_ISSUER:?JWT_ISSUER is required}"
JWT_AUDIENCE="${JWT_AUDIENCE:?JWT_AUDIENCE is required}"
SITE_DOMAIN="${SITE_DOMAIN:?SITE_DOMAIN is required for Traefik MCP routing}"
DOCKER_SOCKET_GID="${DOCKER_SOCKET_GID:?DOCKER_SOCKET_GID is required}"
FAIL2BAN_LOG_GID="${FAIL2BAN_LOG_GID:?FAIL2BAN_LOG_GID is required}"

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
    FAIL2BAN_LOG_GID

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
printf "🐳 Docker socket GID: %s\n" "$DOCKER_SOCKET_GID"
printf "🧾 Fail2ban log GID: %s\n" "$FAIL2BAN_LOG_GID"
COMPOSE_ARGS=(-f "$COMPOSE_FILE")
if [[ "$ENABLE_FAIL2BAN_SOCKET" == "true" ]]; then
    if [[ ! -f "$FAIL2BAN_COMPOSE_FILE" ]]; then
        log_error "Fail2ban Compose override not found: $FAIL2BAN_COMPOSE_FILE"
        exit 1
    fi
    COMPOSE_ARGS+=(-f "$FAIL2BAN_COMPOSE_FILE")
    printf "🧾 Fail2ban socket override: %s\n" "$FAIL2BAN_COMPOSE_FILE"
else
    log_warn "Fail2ban socket override disabled because ENABLE_FAIL2BAN_SOCKET=false"
fi
printf "📁 State directory: %s\n" "$STATE_DIR"

verify_authenticated_mcp_health() {
    docker compose "${COMPOSE_ARGS[@]}" exec -T app uv run python - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

from joserfc import jwt
from joserfc.jwk import OctKey

algorithm = os.environ.get("JWT_ALGORITHM", "HS256")
now = int(time.time())
token = jwt.encode(
    {"alg": algorithm, "typ": "JWT"},
    {
        "iss": os.environ["JWT_ISSUER"],
        "aud": os.environ["JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
        "sub": "deploy-healthcheck",
        "client_id": "deploy-healthcheck",
        "client_type": "deploy",
        "scope": "mcp.health.read",
    },
    OctKey.import_key(os.environ["JWT_SHARED_SECRET"]),
    algorithms=[algorithm],
)
payload = json.dumps(
    {"jsonrpc": "2.0", "id": "deploy-health", "method": "tools/list", "params": {}}
).encode("utf-8")
port = os.environ.get("MCP_PORT", "8001")
path = os.environ.get("MCP_PATH", "/mcp")
request = urllib.request.Request(
    f"http://127.0.0.1:{port}{path}",
    data=payload,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        response_body = response.read().decode("utf-8")
except urllib.error.HTTPError as error:
    sys.stderr.write(error.read().decode("utf-8", errors="replace"))
    raise

response_payload = json.loads(response_body)
if "error" in response_payload:
    raise SystemExit(response_payload["error"])
tool_names = {
    tool.get("name")
    for tool in response_payload.get("result", {}).get("tools", [])
    if isinstance(tool, dict)
}
if "get_mcp_health_check" not in tool_names:
    raise SystemExit("authenticated tools/list did not expose get_mcp_health_check")
PY
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
deploy_step "🔍" 3 9 "Verify release image exists"
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log_error "Image not found locally: $IMAGE_NAME"
    log_info "Build it first with TAG=$TAG infra/scripts/release/build.sh"
    exit 1
fi
printf "✅ Release image found: %s\n" "$IMAGE_NAME"

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

# Step 8: start or update the application container with the selected image.
deploy_step "🚀" 8 9 "Start application container"
if [[ "$ENABLE_FAIL2BAN_SOCKET" == "true" ]]; then
    docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate fail2ban-proxy
    printf "✅ Fail2ban proxy container recreated\n"
fi
docker compose "${COMPOSE_ARGS[@]}" up -d --force-recreate app
printf "✅ Application container recreated\n"

# Step 9: verify the app process accepts an authenticated MCP request.
deploy_step "🩺" 9 9 "Verify authenticated MCP health"
for attempt in {1..30}; do
    if verify_authenticated_mcp_health >/dev/null 2>&1; then
        # Step 9: record the currently deployed tag after the health check passes.
        printf "%s\n" "$TAG" > "$STATE_DIR/current_tag"
        printf "✅ Authenticated MCP health check passed\n"
        printf "🎉 Deploy complete: %s\n" "$IMAGE_NAME"
        exit 0
    fi

    printf "⏳ Waiting for app to pass authenticated MCP health (%s/30)\n" "$attempt"
    sleep 2
done

log_error "Deployment failed authenticated MCP health check."
docker compose "${COMPOSE_ARGS[@]}" ps
exit 1
