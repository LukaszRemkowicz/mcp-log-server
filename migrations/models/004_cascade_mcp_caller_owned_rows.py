from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_caller_id_mcp_callers";
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_session_id_agent_sessions";
        ALTER TABLE "agent_sessions" DROP CONSTRAINT IF EXISTS "agent_sessions_caller_id_fkey";
        ALTER TABLE "collect_logs" DROP CONSTRAINT IF EXISTS "collect_logs_session_id_fkey";
        ALTER TABLE "agent_sessions" ADD CONSTRAINT "agent_sessions_caller_id_fkey" FOREIGN KEY ("caller_id") REFERENCES "mcp_callers" ("id") ON DELETE CASCADE;
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_caller_id_mcp_callers" FOREIGN KEY ("caller_id") REFERENCES "mcp_callers" ("id") ON DELETE CASCADE;
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_session_id_agent_sessions" FOREIGN KEY ("session_id") REFERENCES "agent_sessions" ("id") ON DELETE CASCADE;
        ALTER TABLE "collect_logs" ADD CONSTRAINT "collect_logs_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "agent_sessions" ("id") ON DELETE CASCADE;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "collect_logs" DROP CONSTRAINT IF EXISTS "collect_logs_session_id_fkey";
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_session_id_agent_sessions";
        ALTER TABLE "agent_calls" DROP CONSTRAINT IF EXISTS "fk_agent_calls_caller_id_mcp_callers";
        ALTER TABLE "agent_sessions" DROP CONSTRAINT IF EXISTS "agent_sessions_caller_id_fkey";
        ALTER TABLE "collect_logs" ADD CONSTRAINT "collect_logs_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "agent_sessions" ("id") ON DELETE RESTRICT;
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_session_id_agent_sessions" FOREIGN KEY ("session_id") REFERENCES "agent_sessions" ("id") ON DELETE RESTRICT;
        ALTER TABLE "agent_calls" ADD CONSTRAINT "fk_agent_calls_caller_id_mcp_callers" FOREIGN KEY ("caller_id") REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT;
        ALTER TABLE "agent_sessions" ADD CONSTRAINT "agent_sessions_caller_id_fkey" FOREIGN KEY ("caller_id") REFERENCES "mcp_callers" ("id") ON DELETE RESTRICT;"""


MODELS_STATE = (
    "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"
)
