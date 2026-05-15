from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "authentications" (
            "id" BIGSERIAL NOT NULL PRIMARY KEY,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "client_id" VARCHAR(255) NOT NULL,
            "client_type" VARCHAR(128) NOT NULL,
            "workspace" VARCHAR(8) NOT NULL,
            "allowed_projects" JSONB NOT NULL,
            CONSTRAINT "uid_authentic_client__6f4972" UNIQUE ("client_id", "client_type", "workspace")
        );
        COMMENT ON COLUMN "authentications"."id" IS 'Database-generated integer id for this allowed MCP caller.';
        COMMENT ON COLUMN "authentications"."created_at" IS 'UTC timestamp when this allowed MCP caller row was created.';
        COMMENT ON COLUMN "authentications"."updated_at" IS 'UTC timestamp when this allowed MCP caller row was last updated.';
        COMMENT ON COLUMN "authentications"."client_id" IS 'Stable client_id claim allowed to call MCP tools.';
        COMMENT ON COLUMN "authentications"."client_type" IS 'Stable client_type claim allowed for this MCP client id.';
        COMMENT ON COLUMN "authentications"."workspace" IS 'MCP workspace this caller is allowed to use.';
        COMMENT ON COLUMN "authentications"."allowed_projects" IS 'Project names this MCP caller row is allowed to access.';
        COMMENT ON TABLE "authentications" IS 'Manually managed MCP caller allowlist entry.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "authentications";"""


MODELS_STATE = "eJyrVsrNT0nNKVayUqiurQUAJbMFEw=="
