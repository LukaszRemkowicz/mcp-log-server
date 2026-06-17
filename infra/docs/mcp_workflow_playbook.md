# MCP Workflow Playbook

This section describes the current MCP workflow surface for the daily
log-analysis agent.

Important response note:

- FastMCP tool results can return both `content` and `structuredContent`
- for agent code, use `result.structuredContent`
- `analyze_daily_log_bundle` currently returns `content: []` and puts the real
  payload only in `structuredContent`
- current workflow entrypoint is a tool, not an MCP prompt
- current workflow skills are exposed as concrete resources, not resource templates

For local development, generate fresh example JWTs with:

```bash
uv run command generate-dev-jwt --output-file .agent/DEV_JWT_TOKENS.json
```

Refresh them when:

- they are older than 24 hours
- token structure or expected scopes change

Then export them into your shell:

```bash
export WORKFLOW_AGENT_JWT="$(jq -r '.workflow_agent' .agent/DEV_JWT_TOKENS.json)"
export CODEX_AGENT_JWT="$(jq -r '.codex_agent' .agent/DEV_JWT_TOKENS.json)"
```

Quick terminal helpers:

```bash
# Pretty-print any MCP JSON body
curl -k -sS http://127.0.0.1:8001/mcp ... | jq

# Print only file content from read_container_file
curl -k -sS http://127.0.0.1:8001/mcp ... | jq -r '.result.structuredContent.content'

# Print only discovered entry names from list_container_directory
curl -k -sS http://127.0.0.1:8001/mcp ... | jq -r '.result.structuredContent.entries[].name'
```

### 1. List Visible Tools

Use this to see which tools are visible for the current JWT.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"1",
    "method":"tools/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it is for:

- confirm JWT-scoped tool visibility
- inspect the current MCP tool surface before calling anything

What it returns right now for the workflow token:

- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `collect_logs`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `analyze_daily_log_bundle`

What it returns right now for the codex token:

- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`
- `collect_logs`
- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `list_projects`
- `get_mcp_service_status`
- `get_mcp_health_check`
- `inspect_containers_health`
- `inspect_container_detail`
- `read_container_file`
- `list_container_directory`

### 2. Get Workflow Bootstrap

Use this as the first workflow-agent call.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2",
    "method":"tools/call",
    "params":{
      "name":"analyze_daily_log_bundle",
      "arguments":{}
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

What it is for:

- first workflow-agent bootstrap step
- returns the main workflow prompt
- returns the available workflow skills
- returns the available workflow tools

What it returns:

- `workflow_name`
- `prompt`
- `mandatory_skills`
- `optional_skills`
- `tools`

### 2a. List Available Projects

Use this to discover which manifest-backed projects are currently available to
the current JWT.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2a",
    "method":"tools/call",
    "params":{
      "name":"list_projects",
      "arguments":{}
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent.result'
```

What it returns:

- `project_name`
- `project_summary`
- `source_keys`

### 2b. Collect Deterministic Logs

Use this when the agent needs manifest-driven log collection for the
authorized project and selected sources.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["nginx","backend"],
        "workspace":"workflow",
        "since":"30m"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

What it is for:

- first deterministic collection surface after the workflow skeleton
- explicit project, source, workspace, and docker/file option tracking
- manifest-driven source resolution before later snapshot reads or searches
- project-scoped snapshot persistence under the configured logs root

Agent-facing collect_logs arguments:

- `project_names`
- `source_keys`
- `workspace`
- optional `session_id`
- `since`
- `until`

Important:

- if `since` is omitted, the server defaults to `24h`
- if `source_keys` is omitted, the server behaves as if `source_keys=["all"]`
- `collect_logs` now always persists per-project artifacts for the requested workspace
- `workspace="workflow"` does not require `session_id`
- `workspace="session"` creates a server-generated `session_id` when the request omits it
- the fixed `workflow-agent` token may only use `workspace="workflow"`; use a
  non-workflow agent token for interactive `workspace="session"` investigations
- use the returned `session_id` for later calls in the same investigation
- reuse the returned `session_id` when the investigation later needs logs from another project
- session follow-up tools use `session_id` plus `project_name`
- `collect_logs` does not search log content; persisted snapshot search happens through `grep_log_snapshot`

What it returns:

- `action`
- `workspace`
- optional `session_id`
- `requested_project_names`
- `projects`
  - each project entry contains its own persisted artifact details such as
    `snapshot_dir`,
    `resolved_source_keys`, `warnings`, `retry_tips`, `collected_at`, and
    `sources`
- `sources` includes per-source deterministic metadata and status
- follow-up file reads and searches happen through the snapshot tools

Example collection call:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-filtered",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["backend"],
        "workspace":"workflow",
        "since":"30m"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Example session collection call that starts a new MCP-owned session:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-session",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage"],
        "source_keys":["backend"],
        "workspace":"session"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

The response includes `session_id`. Reuse that returned value to add another
project into the same investigation session:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-session-batch",
    "method":"tools/call",
    "params":{
      "name":"collect_logs",
      "arguments":{
        "project_names":["landingpage","traefik"],
        "source_keys":["backend"],
        "workspace":"session",
        "session_id":"<returned_session_id>"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Close the interactive session when the investigation is done:

```bash
curl -sS \
  -H 'Authorization: Bearer <codex_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"close-session",
    "method":"tools/call",
    "params":{
      "name":"close_agent_session",
      "arguments":{
        "session_id":"<returned_session_id>"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Snapshot follow-up tools:

- `list_log_snapshot_files`
- `read_log_snapshot_file`
- `grep_log_snapshot`
- `create_filtered_view`
- `group_errors`
- `build_incident_bundle`
- `suggest_followup_window`

Cleaned-view guidance:

- `collect_logs` always preserves the raw snapshot as the source of truth
- `create_filtered_view` builds a smaller deterministic cleaned view from that
  raw snapshot
- filtering works best when sources emit consistent structured logs
- the actual filtering route comes from manifest metadata:
  - `parser_type`
  - `normalization_profile`
  - `default_noise_profile`
- the manifest chooses which built-in noise profile should apply per source,
  and the filtering service applies those rules deterministically

Artifact lookup guidance:

- workflow artifact lookup is database-only; `workflow_inventory.json` is not
  written or read
- use `project_name` alone when you want the newest workflow artifact
- use `archive_name` plus `project_name` when you want to keep reading or
  grepping the same archived workflow artifact later
- use `session_id` plus `project_name` for session workspaces
- call `close_agent_session` when an interactive session investigation is done;
  this marks audit metadata only and keeps existing snapshot files readable

List files from the latest workflow artifact:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-list-snapshot",
    "method":"tools/call",
    "params":{
      "name":"list_log_snapshot_files",
      "arguments":{
        "project_name":"landingpage"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Read one saved log file:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-read-snapshot",
    "method":"tools/call",
    "params":{
      "name":"read_log_snapshot_file",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_bytes":4000
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq -r '.result.structuredContent.content'
```

Search one saved snapshot with controlled extended-regex grep semantics:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-grep-snapshot",
    "method":"tools/call",
    "params":{
      "name":"grep_log_snapshot",
      "arguments":{
        "project_name":"landingpage",
        "grep":"Ban|wp-login|502",
        "source_keys":["fail2ban","nginx_access","traefik_access"],
        "max_matches":100
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Use exactly one source selector: `source_key` for a single source, or
`source_keys` for multiple sources such as `["backend","nginx"]`. Use
`max_matches` with `match_offset` to page larger result sets.

Create one deterministic cleaned view from a saved raw artifact:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-create-filtered-view",
    "method":"tools/call",
    "params":{
      "name":"create_filtered_view",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_lines":100
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

`create_filtered_view`, `group_errors`, and `build_incident_bundle` accept
`source_key` for one source and `source_keys` for multiple sources. Do not pass
both in the same call.

Group repeated error-like findings from one saved snapshot:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-group-errors",
    "method":"tools/call",
    "params":{
      "name":"group_errors",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_groups":20
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Build one compact incident bundle from a saved snapshot:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-incident-bundle",
    "method":"tools/call",
    "params":{
      "name":"build_incident_bundle",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "max_groups":20
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Suggest a narrower recollection window from grouped-analysis timestamps:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2b-followup-window",
    "method":"tools/call",
    "params":{
      "name":"suggest_followup_window",
      "arguments":{
        "first_timestamp":"2026-04-29T10:00:00Z",
        "last_timestamp":"2026-04-29T10:05:00Z",
        "padding_minutes":5
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

### 2c. Inspect Allowed Container Paths

Use these specialist tools when an agent needs to verify deployed project files
inside an approved container.

Important:

- these tools are part of the workflow bootstrap inventory when the workflow
  token includes `container.files.read`
- they are also available to the codex token because it includes
  `container.files.read`
- `source_key` is the manifest container alias such as `backend`, `frontend`,
  `nginx`, or `traefik`
- `path` is the filesystem path inside that container

List an allowed directory:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-list",
    "method":"tools/call",
    "params":{
      "name":"list_container_directory",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "path":"/app"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

Read one allowed file:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-read",
    "method":"tools/call",
    "params":{
      "name":"read_container_file",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"backend",
        "path":"/app/manage.py",
        "max_bytes":4000
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq -r '.result.structuredContent.content'
```

List one allowed file path:

```bash
curl -k -sS \
  -H 'Authorization: Bearer '"$CODEX_AGENT_JWT" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"2c-list-file",
    "method":"tools/call",
    "params":{
      "name":"list_container_directory",
      "arguments":{
        "project_name":"landingpage",
        "source_key":"nginx",
        "path":"/etc/nginx/nginx.conf"
      }
    }
  }' \
  http://127.0.0.1:8001/mcp | jq '.result.structuredContent'
```

The manifest path whitelist still applies. For example:

- `backend` and `frontend` can inspect approved `/app/...` paths
- `nginx` can inspect approved `/etc/nginx/...` paths
- `traefik` can inspect approved `/etc/traefik/...` paths

Current write layout:

- `LOGS_DIR` is the logs root
- workflow collections write under:
  - `<LOGS_DIR>/workflow/<project_key>/latest/`
  - `<LOGS_DIR>/workflow/<project_key>/archive/<archive_name>/`
- session collections write under:
  - `<LOGS_DIR>/sessions/<session_id>/<project_key>/`

### 3. List Concrete Resources

Use this to inspect directly registered resources.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"3",
    "method":"resources/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- the fixed workflow skill resources, for example:
  - `skill://workflow/severity_guide`
  - `skill://workflow/bot_detection`

### 4. List Resource Templates

Use this to discover parameterized resource URIs.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"4",
    "method":"resources/templates/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- one template:
  - `skill://workflow/{skill_name}`

That template exists so invalid workflow skill reads can return structured
agent guidance instead of falling through to a generic unknown-resource error.

### 5. Read One Workflow Skill Resource

Use this when the LLM or agent decides it needs one specific skill fragment.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"5",
    "method":"resources/read",
    "params":{
      "uri":"skill://workflow/severity_guide"
    }
  }' \
  http://127.0.0.1:8001/mcp
```

What it is for:

- load only the selected skill text
- avoid sending all skill content to the LLM up front

What it returns:

- the text contents for `skill://workflow/severity_guide`

### 6. List Prompts

Use this to verify whether the server currently exposes MCP prompts.

Command:

```bash
curl -sS \
  -H 'Authorization: Bearer <workflow_agent_jwt>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{
    "jsonrpc":"2.0",
    "id":"6",
    "method":"prompts/list",
    "params":{}
  }' \
  http://127.0.0.1:8001/mcp
```

What it returns right now:

- an empty `prompts` list

That is expected. The workflow currently uses `tools/call` for
`analyze_daily_log_bundle`, not `prompts/get`.

### Current Workflow Sequence

Current intended agent sequence:

1. optionally call `tools/list` to inspect the visible tool surface for the JWT
2. call `analyze_daily_log_bundle`
3. read `result.structuredContent.prompt`
4. read `result.structuredContent.mandatory_skills`
5. read `result.structuredContent.optional_skills`
6. read `result.structuredContent.tools`
7. always include the mandatory baseline in the LLM input
8. let the LLM decide whether an optional skill is needed
9. if needed, call `resources/read` for the returned skill URI
10. send the assembled prompt plus selected skill text plus deterministic data to the LLM
