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

IMAGE_NAME="${ENVIRONMENT}-mcp-log-server:${TAG}"
STATE_DIR="$(get_state_dir "$ENVIRONMENT")"
NO_CACHE="${NO_CACHE:-false}"
EMERGENCY="${EMERGENCY:-false}"

mkdir -p "$STATE_DIR"

log_header "Building $IMAGE_NAME"
log_info "State directory: $STATE_DIR"

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

# Step 4: verify Docker can inspect the image that was just built.
log_step 4 6 "Verify built image exists"
docker image inspect "$IMAGE_NAME" >/dev/null

# Step 5: record the built tag for local operator visibility.
log_step 5 6 "Record built tag"
printf "%s\n" "$TAG" > "$STATE_DIR/built_tag"

# Step 6: prune older local images for this repository, keeping recent history.
log_step 6 6 "Prune older local images"
prune_local_images "${ENVIRONMENT}-mcp-log-server" "$TAG"

log_success "Build complete: $IMAGE_NAME"
