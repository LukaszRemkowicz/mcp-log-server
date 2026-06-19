"""Factory Boy factories for test-only Tortoise model instances."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import factory
from tortoise import Tortoise
from tortoise.models import Model

from core.types import LogWorkspace
from database.models import AgentSession, CollectLogs, CollectLogsSource, McpCaller, ProjectManifest
from database.types import AgentSessionStatus, CollectLogsSourceStatus, LogSourceType, LogStream
from services.session_ids import generate_session_id

Tortoise.init_models(["database.models"], "models")


class TortoiseModelFactory(factory.Factory):
    """Build Tortoise model instances usable as Factory Boy subfactory relations."""

    class Meta:
        abstract = True

    @classmethod
    def _build(cls, model_class: type[Model], *args: object, **kwargs: object) -> Model:
        obj = model_class(*args, **kwargs)
        cls._mark_saved_for_fk_build(obj)
        return obj

    @classmethod
    def _create(cls, model_class: type[Model], *args: object, **kwargs: object) -> Model:
        return cls._build(model_class, *args, **kwargs)

    @classmethod
    def _mark_saved_for_fk_build(cls, obj: Model) -> None:
        """Mark built related Tortoise objects as usable in FK constructors.

        Tortoise 0.25 does not have Model.construct(), so Factory Boy build()
        cannot pass a SubFactory relation into another Tortoise model unless the
        related object looks saved. Keep that compatibility shim local to this
        abstract test factory layer.
        """

        obj._saved_in_db = True


class McpCallerFactory(TortoiseModelFactory):
    """Build MCP caller model instances without persisting them."""

    class Meta:
        model = McpCaller

    id = factory.Sequence(lambda value: value + 100)
    client_id = factory.Sequence(lambda value: f"test-client-{value}")
    client_type = "agent"
    workspace = LogWorkspace.SESSION
    allowed_projects = factory.LazyFunction(lambda: ["landingpage"])

    @classmethod
    async def save_to_db(cls, **kwargs: Any) -> McpCaller:
        """Create a real MCP caller in the test database."""

        obj = cls.build(**kwargs)
        return await McpCaller.objects.create(
            client_id=obj.client_id,
            client_type=obj.client_type,
            workspace=obj.workspace,
            allowed_projects=obj.allowed_projects,
        )


class AgentSessionFactory(TortoiseModelFactory):
    """Build agent session model instances without persisting them."""

    class Meta:
        model = AgentSession

    id = factory.Sequence(lambda value: value + 1)
    name = factory.LazyFunction(generate_session_id)
    caller = factory.SubFactory(McpCallerFactory)
    status = AgentSessionStatus.ACTIVE

    @classmethod
    async def save_to_db(
        cls,
        *,
        caller: McpCaller | None = None,
        **kwargs: Any,
    ) -> AgentSession:
        """Create a real agent session in the test database."""

        obj = cls.build(**kwargs)
        session_caller = caller or await McpCallerFactory.save_to_db()
        return await AgentSession.objects.create(
            name=obj.name,
            caller=session_caller,
            status=obj.status,
        )


class ProjectManifestFactory(TortoiseModelFactory):
    """Build project manifest model instances without persisting them."""

    class Meta:
        model = ProjectManifest

    project_key = factory.Sequence(lambda value: f"test-project-{value}")
    project_summary = factory.Sequence(lambda value: f"Test project {value}.")
    static_asset_paths = factory.LazyFunction(list)
    static_asset_extensions = factory.LazyFunction(list)
    deployment = None
    sources = factory.LazyFunction(list)

    @classmethod
    async def save_to_db(cls, **kwargs: Any) -> ProjectManifest:
        """Create a real project manifest in the test database."""

        obj = cls.build(**kwargs)
        return await ProjectManifest.objects.create(
            project_key=obj.project_key,
            project_summary=obj.project_summary,
            static_asset_paths=obj.static_asset_paths,
            static_asset_extensions=obj.static_asset_extensions,
            deployment=obj.deployment,
            sources=obj.sources,
        )


class CollectLogsFactory(TortoiseModelFactory):
    """Build collect_logs model instances without persisting them."""

    class Meta:
        model = CollectLogs

    workspace = LogWorkspace.SESSION
    session = factory.SubFactory(AgentSessionFactory)
    project_name = factory.Sequence(lambda value: f"test-project-{value}")
    collected_at = factory.LazyFunction(lambda: datetime(2026, 5, 9, 12, 30, tzinfo=UTC))
    snapshot_dir = factory.Sequence(lambda value: f"/tmp/test-snapshot-{value}")
    requested_source_keys = factory.LazyFunction(lambda: ["backend"])
    resolved_source_keys = factory.LazyFunction(lambda: ["backend"])
    unknown_requested_source_keys = factory.LazyFunction(list)
    requested_since = "24h"
    requested_until = None
    warnings = factory.LazyFunction(list)
    retry_tips = factory.LazyFunction(list)
    is_latest = True

    @classmethod
    async def save_to_db(
        cls,
        *,
        session: AgentSession | None = None,
        **kwargs: Any,
    ) -> CollectLogs:
        """Create a real collect_logs row in the test database."""

        obj = cls.build(session=session or AgentSessionFactory.build(), **kwargs)
        agent_session = session or await AgentSessionFactory.save_to_db()
        return await CollectLogs.objects.create(
            workspace=obj.workspace,
            session=agent_session,
            project_name=obj.project_name,
            collected_at=obj.collected_at,
            snapshot_dir=obj.snapshot_dir,
            requested_source_keys=obj.requested_source_keys,
            resolved_source_keys=obj.resolved_source_keys,
            unknown_requested_source_keys=obj.unknown_requested_source_keys,
            requested_since=obj.requested_since,
            requested_until=obj.requested_until,
            warnings=obj.warnings,
            retry_tips=obj.retry_tips,
            is_latest=obj.is_latest,
        )


class CollectLogsSourceFactory(TortoiseModelFactory):
    """Build collect_logs source model instances without persisting them."""

    class Meta:
        model = CollectLogsSource

    collect_logs = factory.SubFactory(CollectLogsFactory)
    source_key = "backend"
    source_type = LogSourceType.DOCKER
    target = "integration-backend"
    description = "Backend integration logs."
    stream: LogStream | None = LogStream.STDOUT
    parser_type: str | None = "python_json"
    normalization_profile: str | None = "backend_app"
    default_noise_profile: str | None = "backend_noise"
    status = CollectLogsSourceStatus.COLLECTED
    file = None
    line_count = 2
    error: str | None = None
    retry_tips = factory.LazyFunction(list)

    @classmethod
    async def save_to_db(
        cls,
        *,
        collect_logs: CollectLogs | None = None,
        **kwargs: Any,
    ) -> CollectLogsSource:
        """Create a real collect_logs source row in the test database."""

        obj = cls.build(collect_logs=collect_logs or CollectLogsFactory.build(), **kwargs)
        snapshot = collect_logs or await CollectLogsFactory.save_to_db()
        return await CollectLogsSource.objects.create(
            collect_logs=snapshot,
            source_key=obj.source_key,
            source_type=obj.source_type,
            target=obj.target,
            description=obj.description,
            stream=obj.stream,
            parser_type=obj.parser_type,
            normalization_profile=obj.normalization_profile,
            default_noise_profile=obj.default_noise_profile,
            status=obj.status,
            file=obj.file,
            line_count=obj.line_count,
            error=obj.error,
            retry_tips=obj.retry_tips,
        )


class UnavailableCollectLogsSourceFactory(CollectLogsSourceFactory):
    """Build unavailable collect_logs source model instances without persisting them."""

    source_key = "nginx"
    source_type = LogSourceType.FILE
    target = "/var/log/nginx/access.log"
    description = "Nginx access logs."
    stream = None
    parser_type = None
    normalization_profile = None
    default_noise_profile = None
    status = CollectLogsSourceStatus.UNAVAILABLE
    file = None
    line_count = 0
    error = "Source file was not available."
    retry_tips = factory.LazyFunction(lambda: ["Check the configured source path."])
