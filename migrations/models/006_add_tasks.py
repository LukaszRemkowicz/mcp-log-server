from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "tasks" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "task_type" VARCHAR(14) NOT NULL,
    "status" VARCHAR(9) NOT NULL DEFAULT 'queued',
    "workspace" VARCHAR(8) NOT NULL,
    "session_id" VARCHAR(24),
    "project_name" VARCHAR(128),
    "arguments" JSONB NOT NULL,
    "result" JSONB,
    "error_code" VARCHAR(128),
    "error_message" TEXT,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "expires_at" TIMESTAMPTZ,
    "caller_id" BIGINT NOT NULL REFERENCES "mcp_callers" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "tasks"."id" IS 'Stable task id returned to MCP clients for polling.';
COMMENT ON COLUMN "tasks"."created_at" IS 'UTC timestamp when this async task row was created.';
COMMENT ON COLUMN "tasks"."updated_at" IS 'UTC timestamp when this async task row was last updated.';
COMMENT ON COLUMN "tasks"."task_type" IS 'Async task kind, for example log_collection.';
COMMENT ON COLUMN "tasks"."status" IS 'Async task lifecycle status.';
COMMENT ON COLUMN "tasks"."workspace" IS 'MCP workspace this task runs within.';
COMMENT ON COLUMN "tasks"."session_id" IS 'Human-readable session_id associated with this async task.';
COMMENT ON COLUMN "tasks"."project_name" IS 'Project associated with this async task, when project-scoped.';
COMMENT ON COLUMN "tasks"."arguments" IS 'Sanitized task input arguments.';
COMMENT ON COLUMN "tasks"."result" IS 'Structured task result payload after successful completion.';
COMMENT ON COLUMN "tasks"."error_code" IS 'Stable error code for failed tasks, when available.';
COMMENT ON COLUMN "tasks"."error_message" IS 'Human-readable failure detail for failed tasks.';
COMMENT ON COLUMN "tasks"."started_at" IS 'UTC timestamp when task execution started.';
COMMENT ON COLUMN "tasks"."completed_at" IS 'UTC timestamp when task execution reached a terminal state.';
COMMENT ON COLUMN "tasks"."expires_at" IS 'UTC timestamp after which this task may be cleaned up.';
COMMENT ON COLUMN "tasks"."caller_id" IS 'Allowed MCP caller that created this async task.';
COMMENT ON TABLE "tasks" IS 'One persisted async MCP task, such as a background log collection.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "tasks";"""


MODELS_STATE = (
    "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"
)
