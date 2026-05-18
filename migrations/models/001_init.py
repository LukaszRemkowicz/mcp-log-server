from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "agent_calls" (
            "id" UUID NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "session_id" BIGINT NOT NULL,
            "session_ended" BOOL NOT NULL DEFAULT False,
            "workspace" VARCHAR(8) NOT NULL,
            "event" VARCHAR(23) NOT NULL,
            "client_id" BIGINT NOT NULL,
            "tool_name" VARCHAR(255),
            "uri" TEXT,
            "duration_seconds" DOUBLE PRECISION,
            "success" BOOL NOT NULL DEFAULT True,
            "error_code" VARCHAR(128),
            "project_name" VARCHAR(255),
            "source_keys" JSONB,
            "arguments" JSONB
        );
        COMMENT ON COLUMN "agent_calls"."id" IS 'Unique UUID for this recorded MCP call row.';
        COMMENT ON COLUMN "agent_calls"."created_at" IS 'UTC timestamp when this MCP call row was created.';
        COMMENT ON COLUMN "agent_calls"."session_id" IS 'Session that owns this recorded MCP call row.';
        COMMENT ON COLUMN "agent_calls"."session_ended" IS 'Whether this row marks the end of the agent session. Planned close_agent_session support will write this explicitly.';
        COMMENT ON COLUMN "agent_calls"."workspace" IS 'Requested log workspace for the call: ''workflow'' for shared scheduled workflow context or ''session'' for an interactive investigation.';
        COMMENT ON COLUMN "agent_calls"."event" IS 'MCP action type, such as mcp_call_tool, mcp_read_resource, mcp_list_tools, or mcp_call_tool_exception.';
        COMMENT ON COLUMN "agent_calls"."client_id" IS 'Allowed MCP caller that created this recorded MCP call row.';
        COMMENT ON COLUMN "agent_calls"."tool_name" IS 'MCP tool name when event records a tool call.';
        COMMENT ON COLUMN "agent_calls"."uri" IS 'MCP resource URI when event records a resource read, such as a workflow skill URI. Tool-call rows leave this empty.';
        COMMENT ON COLUMN "agent_calls"."duration_seconds" IS 'Measured call duration in seconds, when timing is available.';
        COMMENT ON COLUMN "agent_calls"."success" IS 'Whether the call completed successfully from the agent audit perspective.';
        COMMENT ON COLUMN "agent_calls"."error_code" IS 'Stable error code for failed or rejected calls, when available.';
        COMMENT ON COLUMN "agent_calls"."project_name" IS 'Project name targeted by the call, when a single project is known.';
        COMMENT ON COLUMN "agent_calls"."source_keys" IS 'Manifest source keys requested or affected by the call, when known.';
        COMMENT ON COLUMN "agent_calls"."arguments" IS 'Sanitized MCP call arguments captured for replay or debugging.';
        COMMENT ON TABLE "agent_calls" IS 'One persisted MCP agent call or move within a session.';
        CREATE TABLE IF NOT EXISTS "mcp_callers" (
            "id" BIGSERIAL NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "client_id" VARCHAR(255) NOT NULL,
            "client_type" VARCHAR(128) NOT NULL,
            "workspace" VARCHAR(8) NOT NULL,
            "allowed_projects" JSONB NOT NULL,
            CONSTRAINT "uid_mcp_callers_client__df1893" UNIQUE ("client_id", "client_type", "workspace")
        );
        COMMENT ON COLUMN "mcp_callers"."id" IS 'Database-generated integer id for this allowed MCP caller.';
        COMMENT ON COLUMN "mcp_callers"."created_at" IS 'UTC timestamp when this allowed MCP caller row was created.';
        COMMENT ON COLUMN "mcp_callers"."updated_at" IS 'UTC timestamp when this allowed MCP caller row was last updated.';
        COMMENT ON COLUMN "mcp_callers"."client_id" IS 'Stable client_id claim allowed to call MCP tools.';
        COMMENT ON COLUMN "mcp_callers"."client_type" IS 'Stable client_type claim allowed for this MCP client id.';
        COMMENT ON COLUMN "mcp_callers"."workspace" IS 'MCP workspace this caller is allowed to use.';
        COMMENT ON COLUMN "mcp_callers"."allowed_projects" IS 'Project names this MCP caller row is allowed to access.';
        COMMENT ON TABLE "mcp_callers" IS 'Manually managed MCP caller allowlist entry.';
        ALTER TABLE "agent_calls"
            ADD CONSTRAINT "fk_agent_calls_client_id_mcp_callers"
            FOREIGN KEY ("client_id") REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT;
        CREATE TABLE IF NOT EXISTS "agent_sessions" (
            "id" BIGSERIAL NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "name" VARCHAR(24) NOT NULL UNIQUE,
            "status" VARCHAR(6) NOT NULL DEFAULT 'active',
            "closed_at" TIMESTAMPTZ,
            "caller_id" BIGINT NOT NULL REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT
        );
        COMMENT ON COLUMN "agent_sessions"."id" IS 'Database-generated integer id for this agent session.';
        COMMENT ON COLUMN "agent_sessions"."created_at" IS 'UTC timestamp when this agent session was created.';
        COMMENT ON COLUMN "agent_sessions"."updated_at" IS 'UTC timestamp when this agent session was last updated.';
        COMMENT ON COLUMN "agent_sessions"."name" IS 'Human-readable session name returned to agents as session_id.';
        COMMENT ON COLUMN "agent_sessions"."status" IS 'Lifecycle status for this agent session.';
        COMMENT ON COLUMN "agent_sessions"."closed_at" IS 'UTC timestamp when this session was closed, if closed.';
        COMMENT ON COLUMN "agent_sessions"."caller_id" IS 'Allowed MCP caller that owns this agent session.';
        COMMENT ON TABLE "agent_sessions" IS 'One agent session owned by one MCP caller.';
        ALTER TABLE "agent_calls"
            ADD CONSTRAINT "fk_agent_calls_session_id_agent_sessions"
            FOREIGN KEY ("session_id") REFERENCES "agent_sessions" ("id") ON DELETE RESTRICT;
        CREATE TABLE IF NOT EXISTS "collect_logs" (
            "id" BIGSERIAL NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "workspace" VARCHAR(8) NOT NULL,
            "project_name" VARCHAR(255) NOT NULL,
            "collected_at" TIMESTAMPTZ NOT NULL,
            "snapshot_dir" TEXT NOT NULL,
            "archive_name" VARCHAR(255),
            "is_latest" BOOL NOT NULL DEFAULT False,
            "requested_source_keys" JSONB NOT NULL,
            "resolved_source_keys" JSONB NOT NULL,
            "unknown_requested_source_keys" JSONB NOT NULL,
            "requested_since" VARCHAR(255),
            "requested_until" VARCHAR(255),
            "warnings" JSONB NOT NULL,
            "retry_tips" JSONB NOT NULL,
            "session_id" BIGINT NOT NULL REFERENCES "agent_sessions" ("id") ON DELETE RESTRICT
        );
        COMMENT ON COLUMN "collect_logs"."id" IS 'Database-generated integer id for this collected log artifact.';
        COMMENT ON COLUMN "collect_logs"."created_at" IS 'UTC timestamp when this collected log metadata row was created.';
        COMMENT ON COLUMN "collect_logs"."workspace" IS 'Collection workspace, currently ''workflow'' or ''session''.';
        COMMENT ON COLUMN "collect_logs"."project_name" IS 'Manifest project key collected by this artifact.';
        COMMENT ON COLUMN "collect_logs"."collected_at" IS 'UTC timestamp when collection metadata was produced.';
        COMMENT ON COLUMN "collect_logs"."snapshot_dir" IS 'Persisted snapshot directory path under the logs root.';
        COMMENT ON COLUMN "collect_logs"."archive_name" IS 'Workflow archive name when this workflow artifact is archived.';
        COMMENT ON COLUMN "collect_logs"."is_latest" IS 'Whether this is the latest workflow artifact for the project.';
        COMMENT ON COLUMN "collect_logs"."requested_source_keys" IS 'Source keys requested by the caller before manifest resolution.';
        COMMENT ON COLUMN "collect_logs"."resolved_source_keys" IS 'Source keys resolved from the manifest and attempted for collection.';
        COMMENT ON COLUMN "collect_logs"."unknown_requested_source_keys" IS 'Requested source keys that were not present in the manifest.';
        COMMENT ON COLUMN "collect_logs"."requested_since" IS 'Original collect_logs since argument.';
        COMMENT ON COLUMN "collect_logs"."requested_until" IS 'Original collect_logs until argument.';
        COMMENT ON COLUMN "collect_logs"."warnings" IS 'Deterministic warnings returned for this project collection.';
        COMMENT ON COLUMN "collect_logs"."retry_tips" IS 'Retry guidance returned for this project collection.';
        COMMENT ON COLUMN "collect_logs"."session_id" IS 'Session that owns this collected log artifact.';
        COMMENT ON TABLE "collect_logs" IS 'Persist metadata for one collect_logs artifact.';
        CREATE TABLE IF NOT EXISTS "collect_logs_sources" (
            "id" BIGSERIAL NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "source_key" VARCHAR(255) NOT NULL,
            "source_type" VARCHAR(6) NOT NULL,
            "target" TEXT NOT NULL,
            "description" TEXT NOT NULL,
            "stream" VARCHAR(6),
            "parser_type" VARCHAR(128),
            "normalization_profile" VARCHAR(128),
            "default_noise_profile" VARCHAR(128),
            "status" VARCHAR(11) NOT NULL,
            "file" VARCHAR(1024),
            "line_count" INT NOT NULL DEFAULT 0,
            "error" TEXT,
            "retry_tips" JSONB NOT NULL,
            "collect_logs_id" BIGINT NOT NULL REFERENCES "collect_logs" ("id") ON DELETE CASCADE
        );
        COMMENT ON COLUMN "collect_logs_sources"."id" IS 'Database-generated integer id for this collected source metadata row.';
        COMMENT ON COLUMN "collect_logs_sources"."created_at" IS 'UTC timestamp when this collected source metadata row was created.';
        COMMENT ON COLUMN "collect_logs_sources"."source_key" IS 'Manifest source key, for example backend, nginx, or frontend.';
        COMMENT ON COLUMN "collect_logs_sources"."source_type" IS 'Manifest source type, currently docker or file.';
        COMMENT ON COLUMN "collect_logs_sources"."target" IS 'Manifest target used for collection, such as container name or file path.';
        COMMENT ON COLUMN "collect_logs_sources"."description" IS 'Human-readable manifest source description.';
        COMMENT ON COLUMN "collect_logs_sources"."stream" IS 'Requested stream metadata, such as stdout or stderr.';
        COMMENT ON COLUMN "collect_logs_sources"."parser_type" IS 'Parser profile from manifest metadata.';
        COMMENT ON COLUMN "collect_logs_sources"."normalization_profile" IS 'Normalization profile used by deterministic analysis tools.';
        COMMENT ON COLUMN "collect_logs_sources"."default_noise_profile" IS 'Default noise profile used by filtering tools.';
        COMMENT ON COLUMN "collect_logs_sources"."status" IS 'Collection status, currently collected or unavailable.';
        COMMENT ON COLUMN "collect_logs_sources"."file" IS 'Logs-root-relative source file path, for example sessions/<session_id>/<project_name>/<source>.log or workflow/<project_name>/latest/<source>.log.';
        COMMENT ON COLUMN "collect_logs_sources"."line_count" IS 'Number of lines persisted for this source.';
        COMMENT ON COLUMN "collect_logs_sources"."error" IS 'Source-level collection error when status is unavailable.';
        COMMENT ON COLUMN "collect_logs_sources"."retry_tips" IS 'Source-level retry guidance when collection failed.';
        COMMENT ON COLUMN "collect_logs_sources"."collect_logs_id" IS 'Parent collect_logs artifact this source file belongs to.';
        COMMENT ON TABLE "collect_logs_sources" IS 'Persist metadata for one source file inside a collect_logs artifact.';
        CREATE TABLE IF NOT EXISTS "project_manifests" (
            "id" UUID NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "project_key" VARCHAR(255) NOT NULL UNIQUE,
            "project_summary" TEXT NOT NULL,
            "static_asset_paths" JSONB NOT NULL,
            "static_asset_extensions" JSONB NOT NULL,
            "sources" JSONB NOT NULL
        );
        COMMENT ON COLUMN "project_manifests"."id" IS 'Unique UUID for this stored manifest row.';
        COMMENT ON COLUMN "project_manifests"."created_at" IS 'UTC timestamp when this manifest row was created.';
        COMMENT ON COLUMN "project_manifests"."updated_at" IS 'UTC timestamp when this manifest row was last updated.';
        COMMENT ON COLUMN "project_manifests"."project_key" IS 'Stable project key from the manifest, for example ''landingpage''.';
        COMMENT ON COLUMN "project_manifests"."project_summary" IS 'Human-readable project summary from the manifest.';
        COMMENT ON COLUMN "project_manifests"."static_asset_paths" IS 'Static asset paths from the manifest used for noise classification.';
        COMMENT ON COLUMN "project_manifests"."static_asset_extensions" IS 'Static asset file extensions from the manifest used for noise classification.';
        COMMENT ON COLUMN "project_manifests"."sources" IS 'List of source definitions with the same shape as Manifest.sources.';
        COMMENT ON TABLE "project_manifests" IS 'Persist one project manifest with the same shape as manifest JSON.';
        CREATE TABLE IF NOT EXISTS "aerich" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "version" VARCHAR(255) NOT NULL,
            "app" VARCHAR(100) NOT NULL,
            "content" JSONB NOT NULL
        );"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = "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"
