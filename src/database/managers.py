"""Django-style managers for Tortoise database models."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, ClassVar

from tortoise.manager import Manager
from tortoise.models import Model


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


class DatabaseModel(Model):
    """Abstract model base that gives every database model an objects manager."""

    objects: ClassVar[ObjectsManager[Any]] = ObjectsManager()

    class Meta:
        abstract = True
