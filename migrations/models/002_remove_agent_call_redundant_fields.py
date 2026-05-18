from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" DROP COLUMN "workspace";
        ALTER TABLE "agent_calls" DROP COLUMN "session_ended";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" ADD "workspace" VARCHAR(8) NOT NULL DEFAULT 'workflow';
        ALTER TABLE "agent_calls" ADD "session_ended" BOOL NOT NULL DEFAULT False;
        COMMENT ON COLUMN "agent_calls"."workspace" IS 'Requested log workspace for the call: ''workflow'' for shared scheduled workflow context or ''session'' for an interactive investigation.';
COMMENT ON COLUMN "agent_calls"."session_ended" IS 'Whether this row marks the end of the agent session. Planned close_agent_session support will write this explicitly.';
"""


MODELS_STATE = (
    "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"
    "2qLdZnv7x8/z/G22Qr"
)
