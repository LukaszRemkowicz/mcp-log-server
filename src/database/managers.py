"""Django-style managers for Tortoise database models."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, ClassVar

from tortoise.manager import Manager
from tortoise.models import Model

from core.types import LogWorkspace


class ObjectsManager[ModelT: Model](Manager):
    """Small Django-style facade over Tortoise model query methods."""

    _model: type[ModelT]

    def create(self, **values: Any) -> Awaitable[ModelT]:
        """Create one model row."""

        return self._model.create(**values)

    def get(self, **filters: Any) -> Awaitable[ModelT]:
        """Return one model row or raise when it does not exist."""

        return self._model.get(**filters)

    def filter(self, **filters: Any) -> Any:
        """Return a Tortoise queryset matching the filters."""

        return self._model.filter(**filters)

    def all(self) -> Any:
        """Return a Tortoise queryset for all rows."""

        return self._model.all()


class CollectLogsManager[ModelT: Model](ObjectsManager[ModelT]):
    """Django-style manager helpers for collect_logs rows."""

    def get_latest(self, project_name: str) -> Awaitable[ModelT | None]:
        """Return the current latest workflow row for one project."""

        return self.filter(
            project_name=project_name,
            workspace=LogWorkspace.WORKFLOW,
            is_latest=True,
        ).first()


class DatabaseModel(Model):
    """Abstract model base that gives every database model an objects manager."""

    objects: ClassVar[ObjectsManager[Any]] = ObjectsManager()

    class Meta:
        abstract = True
