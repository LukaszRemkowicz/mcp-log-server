# Landingpage Media Connector Design

## Summary

Add one read-only MCP tool that lets an agent inspect landingpage media state and
answer whether disk files appear safe to delete. The tool will not inspect the
landingpage database or filesystem directly. Instead, it will call a narrow
Unix-socket connector, and that connector will run an allowlisted Django
management command inside the landingpage runtime.

The accepted flow is:

```text
agent -> MCP tool -> landingpage Django socket connector -> Django management command -> JSON report
```

This keeps landingpage-specific model and storage knowledge inside landingpage,
while keeping MCP responsible for authorization, tool shape, error handling, and
agent-facing response structure.

## Goals

- Provide one MCP tool for landingpage media inventory and cleanup analysis.
- Let the agent summarize what media exists in the DB and on disk.
- Report missing DB-referenced files and unreferenced disk files.
- Flag delete candidates without deleting anything.
- Avoid public REST endpoints for operational media inspection.
- Avoid giving MCP direct landingpage database credentials.
- Make future landingpage operational checks easy to add as new fixed connector
  operations backed by Django management commands.

## Non-Goals

- No delete, move, rewrite, or cleanup action.
- No generic remote shell execution through MCP or the connector.
- No public Django API endpoint.
- No direct landingpage DB schema replication in `mcp-log-server`.
- No broad Docker socket access for this feature.

## Architecture

### MCP Tool

Add one tool:

```text
inspect_landingpage_media_inventory
```

The tool will be read-only, idempotent, and project-authorized like the existing
inspection tools. It will call a new service client that speaks to a
landingpage Django socket connector over a shared Unix socket path.

The tool response should include:

- action name
- requested project name
- connector status
- inventory summary counts
- DB media references grouped by model and field
- disk media summary grouped by directory or media bucket
- missing file references
- unreferenced disk files as delete candidates
- warnings and truncation flags
- next-step tips for agent interpretation

The MCP server should not decide deletion safety beyond deterministic
classification. The agent can explain the findings and recommend whether a
human should review the delete candidates.

### Landingpage Media Socket Operation

Add fixed landingpage media operations to the generic socket-app pattern.

The generic socket app listens on:

```text
/run/socket-app/gateway.sock
```

The protocol follows the existing line-delimited JSON request style:

```json
{"operation":"media_inventory","params":{"include_disk":true,"include_orphan_candidates":true}}
```

The socket app must expose only allowlisted operations. For this feature, the
first operation is:

```text
media_inventory
```

It must not accept arbitrary command strings, shell fragments, environment
overrides, or file paths from the agent.

### Django Management Command

Landingpage will own management commands such as:

```bash
python manage.py mcp_list_commands --json
python manage.py media_inventory --json
```

The connector invokes these fixed commands and reads one JSON document from
stdout. `mcp_list_commands` should describe the available fixed Django-backed
operations for the agent. `media_inventory` should use Django model metadata and
storage APIs to inspect current media state. This allows the inventory to
discover current `ImageField` and `FileField` fields, including fields on
`AstroImage`, `User`, `ShopProduct`, `ShopSettings`, and generated image
variant models, without duplicating model knowledge in MCP.

The command should return a stable JSON shape with:

- schema version
- generated timestamp
- media root or storage backend summary
- discovered model fields
- DB references with model, object id, field name, and file name
- disk files with path, size, modified timestamp, and bucket
- missing references
- unreferenced files
- skipped paths and warnings
- truncation flags when limits are hit

The command may pre-compute orphan candidates because Django understands its own
storage layout best. MCP will preserve these deterministic facts and expose them
to the agent.

## Data Flow

1. The agent calls `inspect_landingpage_media_inventory`.
2. MCP verifies the caller is authorized for `landingpage`.
3. MCP sends `media_inventory` to the landingpage Django connector over the Unix
   socket.
4. The connector runs the fixed Django management command with a timeout.
5. Django returns a JSON inventory report.
6. The connector validates that stdout is JSON and wraps errors in a standard
   connector error response.
7. MCP validates the connector payload into typed response models.
8. MCP returns structured content for the agent to summarize.

## Security Boundaries

- The MCP tool is read-only.
- The connector exposes fixed operations, not shell access.
- The connector should run with the least privileges needed to execute the
  landingpage command.
- Media volume access should be read-only when the runtime permits it.
- The connector should communicate over a shared Unix socket volume, not a
  public port.
- The MCP app should not mount landingpage DB credentials for this feature.
- The Django command should never delete files or mutate DB rows.
- Connector errors should not leak secrets from environment variables, settings,
  command lines, or tracebacks.

## Error Handling

MCP should return structured tool errors for:

- connector socket unavailable
- connector timeout
- unsupported connector operation
- Django command non-zero exit
- invalid JSON from the command
- schema validation failure
- landingpage command reports unavailable storage or database access

Each error should include retry tips, for example:

- verify the connector container is running
- verify the shared socket volume is mounted
- verify the landingpage backend runtime can run the management command
- retry with smaller limits if the report is truncated

## Extensibility

The connector is intended to support future landingpage operational checks.
Each new check should add:

- one Django management command or command mode in landingpage
- one fixed connector operation
- one typed MCP service method or MCP tool

Examples could include cache state inspection, translation queue summaries, or
image-processing backlog reports. These should remain separate allowlisted
operations, not arguments to a generic shell runner.

## Testing

MCP-side tests should cover:

- tool registration and read-only annotations
- authorization behavior for `landingpage`
- socket client request/response parsing
- connector unavailable and timeout errors
- invalid JSON and command-error responses
- typed response model validation
- API-level tool response shape with a fake connector client

Connector-side tests should cover:

- accepted `media_inventory` operation
- rejected unknown operations
- command timeout
- command non-zero exit
- invalid command stdout
- successful JSON wrapping

Landingpage-side tests should cover:

- model field discovery
- DB reference extraction
- disk file listing
- missing reference detection
- unreferenced disk file detection
- output bounds and truncation flags

## Open Integration Notes

The implementation will likely touch both repositories:

- `mcp-log-server` for the MCP tool, socket client, connector packaging, Compose
  wiring, docs, and tests.
- `landingpage` for the Django `media_inventory` command and command tests.

The connector code lives in `mcp-log-server` inside the generic `socket-app`,
while the Django inventory command lives in `landingpage`.
