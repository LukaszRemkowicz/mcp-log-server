#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
FIXTURES_DIR="$TMP_ROOT/fixtures"
MANIFESTS_DIR="$TMP_ROOT/manifests"
LOGS_DIR="$TMP_ROOT/logs"
SERVER_LOG="$TMP_ROOT/server.log"
TOKENS_FILE="$TMP_ROOT/dev_jwt_tokens.json"
PORT="${PORT:-18081}"
HOST="${HOST:-127.0.0.1}"
BASE_URL="http://${HOST}:${PORT}/mcp"
SESSION_ID="gentle-river-finds-a8f2"

export DATABASE_HOST="${DATABASE_HOST:-127.0.0.1}"
export DATABASE_PORT="${DATABASE_PORT:-${DATABASE_PORT_HOST:-5437}}"
export DATABASE_NAME="${E2E_DATABASE_NAME:-mcp_log_server_test}"
export DATABASE_USER="${DATABASE_USER:-mcp_log_server}"
export DATABASE_PASSWORD="${DATABASE_PASSWORD:-mcp-log-server-local-password}"

if [[ "$DATABASE_NAME" != *_test ]]; then
  echo "Refusing to run HTTP E2E against non-test database: $DATABASE_NAME" >&2
  exit 1
fi

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

mkdir -p "$FIXTURES_DIR" "$MANIFESTS_DIR" "$LOGS_DIR"

cat > "$FIXTURES_DIR/app_first.log" <<'EOF'
alpha
shared match
omega
EOF

cat > "$FIXTURES_DIR/app_second.log" <<'EOF'
beta
shared match two
shared match three
EOF

cat > "$MANIFESTS_DIR/landingpage.json" <<EOF
{
  "project_key": "landingpage",
  "project_summary": "Temporary project for HTTP MCP end-to-end checks.",
  "sources": [
    {
      "source_key": "app_first",
      "source_type": "file",
      "target": "$FIXTURES_DIR/app_first.log",
      "description": "First file-backed application log source.",
      "required": true,
      "parser_type": "plain_text",
      "normalization_profile": "app_logs",
      "retention_class": "short",
      "default_noise_profile": "app_noise",
      "inspect_path_prefixes": []
    },
    {
      "source_key": "app_second",
      "source_type": "file",
      "target": "$FIXTURES_DIR/app_second.log",
      "description": "Second file-backed application log source.",
      "required": true,
      "parser_type": "plain_text",
      "normalization_profile": "app_logs",
      "retention_class": "short",
      "default_noise_profile": "app_noise",
      "inspect_path_prefixes": []
    }
  ]
}
EOF

json_post() {
  local payload="$1"
  json_post_with_token "$WORKFLOW_AGENT_JWT" "$payload"
}

json_post_with_token() {
  local token="$1"
  local payload="$2"
  curl -fsS "$BASE_URL" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -d "$payload"
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "Assertion failed: $message" >&2
    echo "Expected: $expected" >&2
    echo "Actual:   $actual" >&2
    exit 1
  fi
}

assert_file_exists() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Expected file to exist: $path" >&2
    exit 1
  fi
}

(
  cd "$REPO_ROOT"
  uv run commands generate-dev-jwt --output-file "$TOKENS_FILE"
  uv run python -m database.ensure_test_database
  uv run migrate >/dev/null
  uv run python - <<'PY'
import asyncio
import json

import asyncpg

from conf import settings


async def main() -> None:
    connection = await asyncpg.connect(
        host=settings.DATABASE_HOST,
        port=settings.DATABASE_PORT,
        user=settings.DATABASE_USER,
        password=settings.DATABASE_PASSWORD,
        database=settings.DATABASE_NAME,
    )
    try:
        for client_id, client_type, workspace in (
            ("workflow-agent", "workflow_agent", "workflow"),
            ("codex-agent", "codex", "session"),
        ):
            await connection.execute(
                """
                INSERT INTO "mcp_callers" (
                    "client_id",
                    "client_type",
                    "workspace",
                    "allowed_projects"
                )
                VALUES ($1, $2, $3, $4::jsonb)
                ON CONFLICT ("client_id", "client_type", "workspace") DO UPDATE
                SET "allowed_projects" = EXCLUDED."allowed_projects",
                    "updated_at" = NOW()
                """,
                client_id,
                client_type,
                workspace,
                json.dumps(["landingpage"]),
            )
    finally:
        await connection.close()


asyncio.run(main())
PY
)
WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' "$TOKENS_FILE")"
CODEX_AGENT_JWT="$(jq -r '.codex_agent' "$TOKENS_FILE")"
export WORKFLOW_AGENT_JWT
export CODEX_AGENT_JWT

(
  cd "$REPO_ROOT/src"
  uv run python -m scripts.main upload-project-manifest-internal \
    --path "$MANIFESTS_DIR" \
    --all >/dev/null
  uv run python -m scripts.main update-project-manifest-internal \
    --path "$MANIFESTS_DIR" \
    --project landingpage >/dev/null
)

(
  cd "$REPO_ROOT/src"
  PORT="$PORT" \
  HOST="$HOST" \
  LOGS_DIR="$LOGS_DIR" \
  uv run python -m main >"$SERVER_LOG" 2>&1
) &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if json_post '{"jsonrpc":"2.0","id":"tools-list","method":"tools/list","params":{}}' >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "MCP HTTP server exited unexpectedly." >&2
  cat "$SERVER_LOG" >&2 || true
  exit 1
fi

TOOLS_RESPONSE="$(json_post '{"jsonrpc":"2.0","id":"tools-list","method":"tools/list","params":{}}')"
COLLECT_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"collect-workflow","method":"tools/call","params":{"name":"collect_logs","arguments":{"project_names":["landingpage"],"workspace":"workflow","source_keys":["app_first","app_second"]}}}'
)"
LIST_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"list-snapshot","method":"tools/call","params":{"name":"list_log_snapshot_files","arguments":{"project_name":"landingpage"}}}'
)"
READ_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"read-first","method":"tools/call","params":{"name":"read_log_snapshot_file","arguments":{"project_name":"landingpage","source_key":"app_first","max_bytes":1000}}}'
)"
GREP_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"grep-shared","method":"tools/call","params":{"name":"grep_log_snapshot","arguments":{"project_name":"landingpage","grep":"shared match","source_keys":["app_first","app_second"]}}}'
)"
FILTERED_VIEW_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"create-filtered-view","method":"tools/call","params":{"name":"create_filtered_view","arguments":{"project_name":"landingpage","source_keys":["app_first","app_second"],"max_lines":10}}}'
)"
GROUP_ERRORS_RESPONSE="$(
  json_post '{"jsonrpc":"2.0","id":"group-errors","method":"tools/call","params":{"name":"group_errors","arguments":{"project_name":"landingpage","source_keys":["app_first","app_second"],"max_groups":10}}}'
)"
SESSION_RESPONSE="$(
  json_post_with_token "$CODEX_AGENT_JWT" '{"jsonrpc":"2.0","id":"collect-session","method":"tools/call","params":{"name":"collect_logs","arguments":{"project_names":["landingpage"],"workspace":"session","session_id":"'"$SESSION_ID"'","source_keys":["app_first"]}}}'
)"

assert_eq \
  "$(printf '%s' "$TOOLS_RESPONSE" | jq -r '.result.tools | map(.name) | index("collect_logs") != null')" \
  "true" \
  "tools/list should expose collect_logs"
assert_eq \
  "$(printf '%s' "$TOOLS_RESPONSE" | jq -r '.result.tools | map(.name) | index("grep_log_snapshot") != null')" \
  "true" \
  "tools/list should expose grep_log_snapshot"
assert_eq \
  "$(printf '%s' "$TOOLS_RESPONSE" | jq -r '.result.tools | map(.name) | index("create_filtered_view") != null')" \
  "true" \
  "tools/list should expose create_filtered_view"
assert_eq \
  "$(printf '%s' "$COLLECT_RESPONSE" | jq -r '.result.isError')" \
  "false" \
  "collect_logs should succeed for workflow collection"
assert_eq \
  "$(printf '%s' "$COLLECT_RESPONSE" | jq -r '.result.structuredContent.projects[0].requested_since')" \
  "24h" \
  "collect_logs should default to 24h"
assert_eq \
  "$(printf '%s' "$COLLECT_RESPONSE" | jq -r '.result.structuredContent.projects[0].resolved_source_keys | join(",")')" \
  "app_first,app_second" \
  "collect_logs should resolve both file sources"

assert_file_exists "$LOGS_DIR/workflow/landingpage/latest/app_first.log"
assert_file_exists "$LOGS_DIR/workflow/landingpage/latest/app_second.log"
if [[ -e "$LOGS_DIR/workflow/landingpage/workflow_inventory.json" ]]; then
  echo "workflow_inventory.json should not be written; DB rows are the workflow index" >&2
  exit 1
fi

assert_eq \
  "$(printf '%s' "$LIST_RESPONSE" | jq -r '.result.structuredContent.files | length')" \
  "2" \
  "list_log_snapshot_files should return both persisted files"
if ! printf '%s' "$READ_RESPONSE" | jq -e '.result.structuredContent.content == "alpha\nshared match\nomega\n"' >/dev/null; then
  echo "Assertion failed: read_log_snapshot_file should return persisted content" >&2
  exit 1
fi
assert_eq \
  "$(printf '%s' "$GREP_RESPONSE" | jq -r '.result.structuredContent.match_count')" \
  "3" \
  "grep_log_snapshot should match across both files"
assert_eq \
  "$(printf '%s' "$GREP_RESPONSE" | jq -r '.result.structuredContent.matched_source_keys | join(",")')" \
  "app_first,app_second" \
  "grep_log_snapshot should report matches from both files"
if [[ "$(printf '%s' "$FILTERED_VIEW_RESPONSE" | jq -r '.result.isError')" != "false" ]]; then
  echo "create_filtered_view response:" >&2
  printf '%s\n' "$FILTERED_VIEW_RESPONSE" | jq . >&2
fi
assert_eq \
  "$(printf '%s' "$FILTERED_VIEW_RESPONSE" | jq -r '.result.isError')" \
  "false" \
  "create_filtered_view should succeed for workflow collection"
assert_eq \
  "$(printf '%s' "$FILTERED_VIEW_RESPONSE" | jq -r '.result.structuredContent.cleaned_lines | length')" \
  "6" \
  "create_filtered_view should return kept lines from both persisted files"
assert_eq \
  "$(printf '%s' "$FILTERED_VIEW_RESPONSE" | jq -r '.result.structuredContent.excluded_line_count')" \
  "0" \
  "create_filtered_view should keep all plain-text lines for unknown noise profiles"
if [[ "$(printf '%s' "$GROUP_ERRORS_RESPONSE" | jq -r '.result.isError')" != "false" ]]; then
  echo "group_errors response:" >&2
  printf '%s\n' "$GROUP_ERRORS_RESPONSE" | jq . >&2
fi
assert_eq \
  "$(printf '%s' "$GROUP_ERRORS_RESPONSE" | jq -r '.result.isError')" \
  "false" \
  "group_errors should succeed for workflow collection"
assert_eq \
  "$(printf '%s' "$GROUP_ERRORS_RESPONSE" | jq -r '.result.structuredContent.action')" \
  "group_errors" \
  "group_errors should return the grouped-error payload"
assert_eq \
  "$(printf '%s' "$GROUP_ERRORS_RESPONSE" | jq -r '.result.structuredContent.matching_line_count')" \
  "0" \
  "group_errors should return zero matches for non-error fixture logs"

assert_eq \
  "$(printf '%s' "$SESSION_RESPONSE" | jq -r '.result.structuredContent.session_id')" \
  "$SESSION_ID" \
  "collect_logs should return the caller-provided session_id"
assert_file_exists "$LOGS_DIR/sessions/$SESSION_ID/landingpage/app_first.log"

echo "HTTP MCP end-to-end checks passed."
