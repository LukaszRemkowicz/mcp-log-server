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
#   TAG=v0.1.0 doppler run -- infra/scripts/release/build.sh --emergency
#
# What this script does:
#   - validates the release environment and SemVer-like tag
#   - refuses dirty working trees unless EMERGENCY=true
#   - builds prod-mcp-log-server:<TAG>
#   - builds mcp-socket:<TAG>
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

while (($#)); do
    case "$1" in
        --emergency)
            EMERGENCY=true
            ;;
        *)
            log_error "Unknown build argument: $1"
            exit 1
            ;;
    esac
    shift
done

PROJECT_DIR="$(get_project_dir)"
ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-prod}")"
validate_release_environment "$ENVIRONMENT"

TAG="${TAG:-$(git -C "$PROJECT_DIR" describe --tags --exact-match 2>/dev/null || true)}"
validate_tag "$TAG"

IMAGE_NAME="${ENVIRONMENT}-mcp-log-server:${TAG}"
SOCKET_APP_IMAGE_NAME="mcp-socket:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
NO_CACHE="${NO_CACHE:-false}"
EMERGENCY="${EMERGENCY:-false}"

mkdir -p "$STATE_DIR"

log_header "Building $IMAGE_NAME"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"
log_info "State directory: $STATE_DIR"
log_info "Socket app image: $SOCKET_APP_IMAGE_NAME"
if [[ "$NO_CACHE" == "true" ]]; then
    log_info "No-cache mode enabled (fresh build)"
fi
if [[ "$EMERGENCY" == "true" ]]; then
    log_warn "Emergency mode enabled: skipping clean working tree enforcement."
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
socket_app_build_args=(
    --pull
    -f "$PROJECT_DIR/docker/socket-app/Dockerfile"
    -t "$SOCKET_APP_IMAGE_NAME"
)

if [[ "$NO_CACHE" == "true" ]]; then
    build_args+=(--no-cache)
    socket_app_build_args+=(--no-cache)
fi

# Step 3: build the tagged image from the repository root.
log_step 3 8 "Build tagged app image"
docker build "${build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $IMAGE_NAME"

# Step 4: build the tagged generic socket app image from the repository root.
log_step 4 8 "Build tagged socket app image"
docker build "${socket_app_build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $SOCKET_APP_IMAGE_NAME"

# Step 6: verify Docker can inspect the images that were just built.
log_step 5 8 "Verify built images exist"
docker image inspect "$IMAGE_NAME" >/dev/null
docker image inspect "$SOCKET_APP_IMAGE_NAME" >/dev/null
log_success "Image available locally: $IMAGE_NAME"
log_success "Image available locally: $SOCKET_APP_IMAGE_NAME"

# Step 7: record the built tag for local operator visibility.
log_step 6 8 "Record built tag"
printf "%s\n" "$TAG" > "$STATE_DIR/built_tag"
log_info "Built tag file: $STATE_DIR/built_tag"

# Step 8: prune older local MCP app images, keeping only this tag.
log_step 7 8 "Prune older local app images"
prune_local_images "${ENVIRONMENT}-mcp-log-server" "$TAG"

# Step 8: prune older local generic socket app images, keeping only this tag.
log_step 8 8 "Prune older local socket app images"
prune_local_images "mcp-socket" "$TAG"

log_success "Build complete: $IMAGE_NAME"
log_success "Build complete: $SOCKET_APP_IMAGE_NAME"
