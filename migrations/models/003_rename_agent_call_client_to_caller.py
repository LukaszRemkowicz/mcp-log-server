from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_client_id_mcp_callers";
        ALTER TABLE "agent_calls" RENAME COLUMN "client_id" TO "caller_id";
        ALTER TABLE "agent_calls" RENAME CONSTRAINT "agent_calls_client_id_not_null" TO "agent_calls_caller_id_not_null";
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_caller_id_mcp_callers" FOREIGN KEY ("caller_id") REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_caller_id_mcp_callers";
        ALTER TABLE "agent_calls" RENAME CONSTRAINT "agent_calls_caller_id_not_null" TO "agent_calls_client_id_not_null";
        ALTER TABLE "agent_calls" RENAME COLUMN "caller_id" TO "client_id";
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_client_id_mcp_callers" FOREIGN KEY ("client_id") REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT;"""


MODELS_STATE = (
    "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"
    "s="
)
