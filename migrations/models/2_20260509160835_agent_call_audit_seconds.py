from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "agent_calls" RENAME COLUMN "duration_ms" TO "duration_seconds";
        ALTER TABLE "agent_calls" RENAME COLUMN "subject" TO "caller_subject";
        UPDATE "agent_calls"
        SET "duration_seconds" = "duration_seconds" / 1000
        WHERE "duration_seconds" IS NOT NULL;
        COMMENT ON COLUMN "agent_calls"."session_ended" IS $$Whether this row marks the end of the agent session. Planned close_agent_session support will write this explicitly.$$;
        COMMENT ON COLUMN "agent_calls"."uri" IS $$MCP resource URI when event records a resource read, such as a workflow skill URI. Tool-call rows leave this empty.$$;
        COMMENT ON COLUMN "agent_calls"."workspace" IS $$Requested log workspace for the call: 'workflow' for shared scheduled workflow context or 'session' for an interactive investigation.$$;
        COMMENT ON COLUMN "agent_calls"."duration_seconds" IS $$Measured call duration in seconds, when timing is available.$$;
        COMMENT ON COLUMN "agent_calls"."caller_subject" IS $$JWT sub claim identifying the authenticated caller, when available.$$;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "agent_calls"
        SET "duration_seconds" = "duration_seconds" * 1000
        WHERE "duration_seconds" IS NOT NULL;
        ALTER TABLE "agent_calls" RENAME COLUMN "duration_seconds" TO "duration_ms";
        ALTER TABLE "agent_calls" RENAME COLUMN "caller_subject" TO "subject";
        COMMENT ON COLUMN "agent_calls"."session_ended" IS $$Whether this row marks the end of the agent session.$$;
        COMMENT ON COLUMN "agent_calls"."uri" IS $$MCP resource URI when event records a resource read, such as a workflow skill URI.$$;
        COMMENT ON COLUMN "agent_calls"."workspace" IS $$Agent workspace for the call, currently either 'session' or 'workflow'.$$;
        COMMENT ON COLUMN "agent_calls"."duration_ms" IS $$Measured call duration in milliseconds, when timing is available.$$;
        COMMENT ON COLUMN "agent_calls"."subject" IS $$Authenticated JWT subject claim for the caller, when available.$$;"""


MODELS_STATE = (
    "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"
)
