#!/usr/bin/env bash

set -euo pipefail

log_header() {
    printf "\n==> %s\n" "$1"
}

log_step() {
    local current="$1"
    local total="$2"
    local message="$3"

    printf "\n[%s/%s] %s\n" "$current" "$total" "$message"
}

log_info() {
    printf "   %s\n" "$1"
}

log_success() {
    printf "OK %s\n" "$1"
}

log_warn() {
    printf "WARN %s\n" "$1" >&2
}

log_error() {
    printf "ERROR %s\n" "$1" >&2
}

get_project_dir() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "$script_dir/../.." && pwd
}

normalize_environment() {
    local environment="${1:-local}"

    case "$environment" in
        local)
            printf "local"
            ;;
        prod | production)
            printf "prod"
            ;;
        *)
            log_error "Unsupported environment: $environment"
            log_info "Allowed environments: local, prod"
            return 1
            ;;
    esac
}

validate_release_environment() {
    local environment
    environment="$(normalize_environment "${1:-prod}")"

    if [[ "$environment" != "prod" ]]; then
        log_error "Release scripts are for prod images only."
        log_info "Use docker compose for local development; local compose has no tagged app image."
        return 1
    fi
}

validate_tag() {
    local tag="${1:-}"

    if [[ -z "$tag" ]]; then
        log_error "TAG is required."
        return 1
    fi

    if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$ ]]; then
        log_error "Invalid TAG: $tag"
        log_info "Expected a version-like tag, for example v1.2.3 or v1.2.3-rc1."
        return 1
    fi
}

get_compose_file() {
    local project_dir="$1"
    local environment
    environment="$(normalize_environment "${2:-local}")"

    if [[ "$environment" == "prod" ]]; then
        printf "%s/docker-compose.prod.yml" "$project_dir"
    else
        printf "%s/docker-compose.yml" "$project_dir"
    fi
}

get_compose_project_name() {
    local environment
    environment="$(normalize_environment "${1:-local}")"

    if [[ "$environment" == "local" ]]; then
        printf "mcp-log-server"
    else
        printf "mcp-log-server-%s" "$environment"
    fi
}

get_state_dir() {
    local environment
    environment="$(normalize_environment "${1:-local}")"

    if [[ -n "${STATE_DIR:-}" ]]; then
        printf "%s" "$STATE_DIR"
        return
    fi

    local preferred="/var/lib/mcp-log-server/$environment"
    if [[ -d "$(dirname "$preferred")" && -w "$(dirname "$preferred")" ]] || [[ "$(id -u)" == "0" ]]; then
        printf "%s" "$preferred"
    else
        printf "%s/.agent/state/%s" "$(get_project_dir)" "$environment"
    fi
}

get_backup_dir() {
    local project_dir="$1"
    local environment
    environment="$(normalize_environment "${2:-local}")"

    if [[ -n "${BACKUP_DIR:-}" ]]; then
        printf "%s" "$BACKUP_DIR"
        return
    fi

    local preferred="/var/backups/mcp-log-server/$environment"
    if [[ -d "$(dirname "$preferred")" && -w "$(dirname "$preferred")" ]] || [[ "$(id -u)" == "0" ]]; then
        printf "%s" "$preferred"
    else
        printf "%s/.agent/backups/db/%s" "$project_dir" "$environment"
    fi
}

confirm_continue() {
    local prompt="$1"

    if [[ "${AUTO_APPROVE:-false}" == "true" ]]; then
        return 0
    fi

    log_warn "$prompt"
    printf "Type yes to continue: "
    read -r answer
    if [[ "$answer" != "yes" ]]; then
        log_warn "Cancelled."
        return 1
    fi
}

prune_local_images() {
    local repository="$1"
    local keep_tag="$2"
    local keep_count="${KEEP_IMAGE_COUNT:-5}"

    local image_ids=()
    local image_id
    while IFS= read -r image_id; do
        [[ -n "$image_id" ]] && image_ids+=("$image_id")
    done < <(
        docker images "$repository" \
            --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedAt}}' \
            | grep -v ":${keep_tag} " \
            | sort -rk 3 \
            | awk '{print $2}' \
            | awk '!seen[$0]++' \
            | tail -n +"$((keep_count + 1))" \
            || true
    )

    if [[ "${#image_ids[@]}" -eq 0 ]]; then
        return 0
    fi

    docker rmi "${image_ids[@]}" >/dev/null || true
}
