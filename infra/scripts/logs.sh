#!/usr/bin/env bash
###############################################################################
# logs.sh
#
# Purpose:
#   Read logs from a running production Compose container without loading
#   docker-compose.prod.yml. This is useful on the VPS when an operator only
#   wants `docker logs` output and does not want Compose to interpolate all
#   required production variables from Doppler.
#
# Usage:
#   infra/scripts/logs.sh app
#   FOLLOW=true infra/scripts/logs.sh app
#   TAIL_LINES=500 infra/scripts/logs.sh db
#
# Notes:
#   - resolves containers by Docker Compose labels first
#   - falls back to the standard <project>-<service>-1 container name
#   - supports production services only through validate_release_environment
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=utils.sh
source "$SCRIPT_DIR/utils.sh"

ENVIRONMENT="$(normalize_environment "${ENVIRONMENT:-prod}")"
validate_release_environment "$ENVIRONMENT"

COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(get_compose_project_name "$ENVIRONMENT")}"
SERVICE_NAME="${1:-app}"
TAIL_LINES="${TAIL_LINES:-200}"
FOLLOW="${FOLLOW:-false}"

container_name="$(
    docker ps \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
        --filter "label=com.docker.compose.service=$SERVICE_NAME" \
        --format '{{.Names}}' \
        | sort \
        | head -n 1
)"

if [[ -z "$container_name" ]]; then
    fallback_name="${COMPOSE_PROJECT_NAME}-${SERVICE_NAME}-1"
    if docker ps --format '{{.Names}}' | grep -Fxq "$fallback_name"; then
        container_name="$fallback_name"
    fi
fi

if [[ -z "$container_name" ]]; then
    log_error "Running container for $COMPOSE_PROJECT_NAME/$SERVICE_NAME was not found."
    log_info "Available containers:"
    docker ps --format '  {{.Names}}'
    exit 1
fi

log_info "Container: $container_name"
if [[ "$FOLLOW" == "true" ]]; then
    exec docker logs --tail "$TAIL_LINES" -f "$container_name"
fi

exec docker logs --tail "$TAIL_LINES" "$container_name"
