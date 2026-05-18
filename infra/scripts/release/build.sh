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
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
NO_CACHE="${NO_CACHE:-false}"
EMERGENCY="${EMERGENCY:-false}"

mkdir -p "$STATE_DIR"

log_header "Building $IMAGE_NAME"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"
log_info "State directory: $STATE_DIR"
if [[ "$NO_CACHE" == "true" ]]; then
    log_info "No-cache mode enabled (fresh build)"
fi

# Step 1: require a clean working tree unless the operator explicitly opts out.
log_step 1 6 "Check working tree"
if [[ "$EMERGENCY" != "true" ]] && [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    log_error "Working tree has uncommitted changes. Set EMERGENCY=true to build anyway."
    git -C "$PROJECT_DIR" status --short
    exit 1
fi

# Step 2: assemble Docker build arguments for the prod app image.
log_step 2 6 "Prepare Docker build arguments"
build_args=(--pull -f "$PROJECT_DIR/docker/app/Dockerfile" -t "$IMAGE_NAME")

if [[ "$NO_CACHE" == "true" ]]; then
    build_args+=(--no-cache)
fi

# Step 3: build the tagged image from the repository root.
log_step 3 6 "Build tagged app image"
docker build "${build_args[@]}" "$PROJECT_DIR"
log_success "Image built: $IMAGE_NAME"

# Step 4: verify Docker can inspect the image that was just built.
log_step 4 6 "Verify built image exists"
docker image inspect "$IMAGE_NAME" >/dev/null
log_success "Image available locally: $IMAGE_NAME"

# Step 5: record the built tag for local operator visibility.
log_step 5 6 "Record built tag"
printf "%s\n" "$TAG" > "$STATE_DIR/built_tag"
log_info "Built tag file: $STATE_DIR/built_tag"

# Step 6: prune older local images for this repository, keeping recent history.
log_step 6 6 "Prune older local images"
prune_local_images "${ENVIRONMENT}-mcp-log-server" "$TAG"

log_success "Build complete: $IMAGE_NAME"
