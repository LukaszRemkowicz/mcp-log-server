#!/usr/bin/env bash
###############################################################################
# release.sh
#
# Purpose:
#   Build and deploy one tagged production MCP log-server release.
#
# Typical usage:
#   TAG=v0.1.0 doppler run -- infra/scripts/release/release.sh
#   AUTO_APPROVE=true TAG=v0.1.0 doppler run -- infra/scripts/release/release.sh
#
# What this script does:
#   - validates the release environment and tag
#   - runs release/build.sh
#   - runs release/deploy.sh
#
# What this script does not do:
#   - does not duplicate build or deploy internals
#   - does not bypass deploy backup, migration, or confirmation behavior
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
export ENVIRONMENT TAG

log_header "Releasing ${ENVIRONMENT}-mcp-log-server:${TAG}"
log_info "Environment: $ENVIRONMENT"
log_info "Release tag: $TAG"
log_info "Project root: $PROJECT_DIR"

log_step 1 2 "Build release image"
"$SCRIPT_DIR/build.sh"

log_step 2 2 "Deploy release image"
"$SCRIPT_DIR/deploy.sh"

log_success "Release complete: ${ENVIRONMENT}-mcp-log-server:${TAG}"
