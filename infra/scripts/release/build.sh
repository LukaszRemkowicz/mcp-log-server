#!/usr/bin/env bash
###############################################################################
# build.sh
#
# Purpose:
#   Build the tagged production MCP log-server image in a deterministic,
#   rollback-friendly way.
#
# Typical usage:
#   TAG=v0.1.0 doppler run -- infra/scripts/release/build.sh
#   NO_CACHE=true TAG=v0.1.0 doppler run -- infra/scripts/release/build.sh
#
# What this script does:
#   - validates the release environment and SemVer-like tag
#   - refuses dirty working trees unless EMERGENCY=true
#   - builds prod-mcp-log-server:<TAG>
#   - builds prod-mcp-docker-socket-app:<TAG>
#   - records the built tag for operator visibility
#   - prunes older local MCP release images
#
# What this script does not do:
#   - does not start containers
#   - does not run migrations
#   - does not update current_tag or deploy state
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

IMAGE_NAME="${ENVIRONMENT}-mcp-log-server:${TAG}"
DOCKER_SOCKET_APP_IMAGE_NAME="${ENVIRONMENT}-mcp-docker-socket-app:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
NO_CACHE="${NO_CACHE:-false}"
EMERGENCY="${EMERGENCY:-false}"

mkdir -p "$STATE_DIR"

log_header "Building $IMAGE_NAME"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"
log_info "State directory: $STATE_DIR"
log_info "Docker socket app image: $DOCKER_SOCKET_APP_IMAGE_NAME"
if [[ "$NO_CACHE" == "true" ]]; then
    log_info "No-cache mode enabled (fresh build)"
fi

# Step 1: require a clean working tree unless the operator explicitly opts out.
log_step 1 8 "Check working tree"
if [[ "$EMERGENCY" != "true" ]] && [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    log_error "Working tree has uncommitted changes. Set EMERGENCY=true to build anyway."
    git -C "$PROJECT_DIR" status --short
    exit 1
fi

# Step 2: assemble Docker build arguments for the prod app image.
log_step 2 8 "Prepare Docker build arguments"
build_args=(--pull -f "$PROJECT_DIR/docker/app/Dockerfile" -t "$IMAGE_NAME")
docker_socket_app_build_args=(
    --pull
    -f "$PROJECT_DIR/docker/docker-socket-app/Dockerfile"
    -t "$DOCKER_SOCKET_APP_IMAGE_NAME"
)

if [[ "$NO_CACHE" == "true" ]]; then
    build_args+=(--no-cache)
    docker_socket_app_build_args+=(--no-cache)
fi

# Step 3: build the tagged image from the repository root.
log_step 3 8 "Build tagged app image"
docker build "${build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $IMAGE_NAME"

# Step 4: build the tagged Docker socket app image from the repository root.
log_step 4 8 "Build tagged Docker socket app image"
docker build "${docker_socket_app_build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $DOCKER_SOCKET_APP_IMAGE_NAME"

# Step 5: verify Docker can inspect the images that were just built.
log_step 5 8 "Verify built images exist"
docker image inspect "$IMAGE_NAME" >/dev/null
docker image inspect "$DOCKER_SOCKET_APP_IMAGE_NAME" >/dev/null
log_success "Image available locally: $IMAGE_NAME"
log_success "Image available locally: $DOCKER_SOCKET_APP_IMAGE_NAME"

# Step 6: record the built tag for local operator visibility.
log_step 6 8 "Record built tag"
printf "%s\n" "$TAG" > "$STATE_DIR/built_tag"
log_info "Built tag file: $STATE_DIR/built_tag"

# Step 7: prune older local MCP app images, keeping only this tag.
log_step 7 8 "Prune older local app images"
prune_local_images "${ENVIRONMENT}-mcp-log-server" "$TAG"

# Step 8: prune older local Docker socket app images, keeping only this tag.
log_step 8 8 "Prune older local Docker socket app images"
prune_local_images "${ENVIRONMENT}-mcp-docker-socket-app" "$TAG"

log_success "Build complete: $IMAGE_NAME"
log_success "Build complete: $DOCKER_SOCKET_APP_IMAGE_NAME"
