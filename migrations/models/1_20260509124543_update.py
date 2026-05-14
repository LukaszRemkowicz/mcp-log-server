from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "collect_logs" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "session_id" UUID,
    "workspace" VARCHAR(8) NOT NULL,
    "project_name" VARCHAR(255) NOT NULL,
    "collected_at" TIMESTAMPTZ NOT NULL,
    "snapshot_dir" TEXT NOT NULL,
    "metadata_file" VARCHAR(1024) NOT NULL,
    "archive_name" VARCHAR(255),
    "is_latest" BOOL NOT NULL DEFAULT False,
    "requested_source_keys" JSONB NOT NULL,
    "resolved_source_keys" JSONB NOT NULL,
    "unknown_requested_source_keys" JSONB NOT NULL,
    "requested_since" VARCHAR(255),
    "requested_until" VARCHAR(255),
    "warnings" JSONB NOT NULL,
    "retry_tips" JSONB NOT NULL
);
COMMENT ON COLUMN "collect_logs"."id" IS 'Unique UUID for this collected log artifact.';
COMMENT ON COLUMN "collect_logs"."created_at" IS 'UTC timestamp when this collected log metadata row was created.';
COMMENT ON COLUMN "collect_logs"."session_id" IS 'Agent session UUID for session workspace collections.';
COMMENT ON COLUMN "collect_logs"."workspace" IS 'Collection workspace, currently ''workflow'' or ''session''.';
COMMENT ON COLUMN "collect_logs"."project_name" IS 'Manifest project key collected by this artifact.';
COMMENT ON COLUMN "collect_logs"."collected_at" IS 'UTC timestamp when collection metadata was produced.';
COMMENT ON COLUMN "collect_logs"."snapshot_dir" IS 'Persisted snapshot directory path under the logs root.';
COMMENT ON COLUMN "collect_logs"."metadata_file" IS 'Path to snapshot_metadata.json or workflow_inventory.json.';
COMMENT ON COLUMN "collect_logs"."archive_name" IS 'Workflow archive name when this workflow artifact is archived.';
COMMENT ON COLUMN "collect_logs"."is_latest" IS 'Whether this is the latest workflow artifact for the project.';
COMMENT ON COLUMN "collect_logs"."requested_source_keys" IS 'Source keys requested by the caller before manifest resolution.';
COMMENT ON COLUMN "collect_logs"."resolved_source_keys" IS 'Source keys resolved from the manifest and attempted for collection.';
COMMENT ON COLUMN "collect_logs"."unknown_requested_source_keys" IS 'Requested source keys that were not present in the manifest.';
COMMENT ON COLUMN "collect_logs"."requested_since" IS 'Original collect_logs since argument.';
COMMENT ON COLUMN "collect_logs"."requested_until" IS 'Original collect_logs until argument.';
COMMENT ON COLUMN "collect_logs"."warnings" IS 'Deterministic warnings returned for this project collection.';
COMMENT ON COLUMN "collect_logs"."retry_tips" IS 'Retry guidance returned for this project collection.';
COMMENT ON TABLE "collect_logs" IS 'Persist metadata for one collect_logs artifact.';
        CREATE TABLE IF NOT EXISTS "collect_logs_sources" (
    "id" UUID NOT NULL PRIMARY KEY,
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
    "collect_logs_id" UUID NOT NULL REFERENCES "collect_logs" ("id") ON DELETE CASCADE
);
COMMENT ON COLUMN "collect_logs_sources"."id" IS 'Unique UUID for this collected source metadata row.';
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
COMMENT ON COLUMN "collect_logs_sources"."file" IS 'Persisted source file path under logs root, when collection succeeded.';
COMMENT ON COLUMN "collect_logs_sources"."line_count" IS 'Number of lines persisted for this source.';
COMMENT ON COLUMN "collect_logs_sources"."error" IS 'Source-level collection error when status is unavailable.';
COMMENT ON COLUMN "collect_logs_sources"."retry_tips" IS 'Source-level retry guidance when collection failed.';
COMMENT ON COLUMN "collect_logs_sources"."collect_logs_id" IS 'Parent collect_logs artifact this source file belongs to.';
COMMENT ON TABLE "collect_logs_sources" IS 'Persist metadata for one source file inside a collect_logs artifact.';
        ALTER TABLE "agent_calls" ALTER COLUMN "workspace" TYPE VARCHAR(8) USING "workspace"::VARCHAR(8);
        ALTER TABLE "agent_calls" ALTER COLUMN "event" TYPE VARCHAR(23) USING "event"::VARCHAR(23);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" ALTER COLUMN "workspace" TYPE VARCHAR(32) USING "workspace"::VARCHAR(32);
        ALTER TABLE "agent_calls" ALTER COLUMN "event" TYPE VARCHAR(128) USING "event"::VARCHAR(128);
        DROP TABLE IF EXISTS "collect_logs_sources";
        DROP TABLE IF EXISTS "collect_logs";"""


MODELS_STATE = (
    "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"
    "GpCg=="
)
