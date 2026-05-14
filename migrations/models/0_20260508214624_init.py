from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "agent_calls" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "session_id" UUID NOT NULL,
    "session_ended" BOOL NOT NULL DEFAULT False,
    "workspace" VARCHAR(32) NOT NULL,
    "event" VARCHAR(128) NOT NULL,
    "subject" VARCHAR(255),
    "client_id" VARCHAR(255),
    "client_type" VARCHAR(128),
    "tool_name" VARCHAR(255),
    "uri" TEXT,
    "duration_ms" DOUBLE PRECISION,
    "success" BOOL NOT NULL DEFAULT True,
    "error_code" VARCHAR(128),
    "project_name" VARCHAR(255),
    "source_keys" JSONB,
    "arguments" JSONB,
    "result_summary" JSONB
);
COMMENT ON COLUMN "agent_calls"."id" IS 'Unique UUID for this recorded MCP call row.';
COMMENT ON COLUMN "agent_calls"."created_at" IS 'UTC timestamp when this MCP call row was created.';
COMMENT ON COLUMN "agent_calls"."session_id" IS 'MCP-generated UUID shared by all rows that belong to one agent session.';
COMMENT ON COLUMN "agent_calls"."session_ended" IS 'Whether this row marks the end of the agent session.';
COMMENT ON COLUMN "agent_calls"."workspace" IS 'Agent workspace for the call, currently either ''session'' or ''workflow''.';
COMMENT ON COLUMN "agent_calls"."event" IS 'MCP action type, such as mcp_call_tool, mcp_read_resource, mcp_list_tools, or mcp_call_tool_exception.';
COMMENT ON COLUMN "agent_calls"."subject" IS 'Authenticated JWT subject claim for the caller, when available.';
COMMENT ON COLUMN "agent_calls"."client_id" IS 'Authenticated FastMCP client id for the caller, when available.';
COMMENT ON COLUMN "agent_calls"."client_type" IS 'JWT client_type claim identifying the caller category, when available.';
COMMENT ON COLUMN "agent_calls"."tool_name" IS 'MCP tool name when event records a tool call.';
COMMENT ON COLUMN "agent_calls"."uri" IS 'MCP resource URI when event records a resource read, such as a workflow skill URI.';
COMMENT ON COLUMN "agent_calls"."duration_ms" IS 'Measured call duration in milliseconds, when timing is available.';
COMMENT ON COLUMN "agent_calls"."success" IS 'Whether the call completed successfully from the agent audit perspective.';
COMMENT ON COLUMN "agent_calls"."error_code" IS 'Stable error code for failed or rejected calls, when available.';
COMMENT ON COLUMN "agent_calls"."project_name" IS 'Project name targeted by the call, when a single project is known.';
COMMENT ON COLUMN "agent_calls"."source_keys" IS 'Manifest source keys requested or affected by the call, when known.';
COMMENT ON COLUMN "agent_calls"."arguments" IS 'Sanitized MCP call arguments captured for replay or debugging.';
COMMENT ON COLUMN "agent_calls"."result_summary" IS 'Sanitized compact summary of the MCP call result.';
COMMENT ON TABLE "agent_calls" IS 'One persisted MCP agent call or move within a session.';
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


MODELS_STATE = (
    "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"
)
