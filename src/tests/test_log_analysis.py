from __future__ import annotations

from pathlib import Path

import pytest

from database.fields import FileReference
from database.schemas import CollectLogsSourceOut
from services import log_analysis
from services.log_analysis import LogAnalysisService, ProxyRouteAccumulator, StatusClass
from tools.models import SnapshotLineReferencePayload


def _proxy_source(path: Path) -> CollectLogsSourceOut:
    return CollectLogsSourceOut(
        id=1,
        source_key="nginx",
        source_type="file",
        target=path.as_posix(),
        description="nginx access log",
        stream=None,
        parser_type="json",
        normalization_profile="proxy_access",
        default_noise_profile=None,
        status="collected",
        file=FileReference(name=path.as_posix()),
        line_count=3,
        error=None,
        retry_tips=[],
    )


def test_proxy_activity_caps_route_accumulators_to_max_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect proxy analysis from unbounded unique route groups."""

    log_file = tmp_path / "nginx.log"
    log_file.write_text(
        "\n".join(
            [
                '{"status": 404, "request": "GET /first HTTP/1.1"}',
                '{"status": 404, "request": "GET /second HTTP/1.1"}',
                '{"status": 502, "request": "POST /third HTTP/1.1"}',
            ]
        ),
        encoding="utf-8",
    )
    created_accumulators = 0

    class CountingProxyRouteAccumulator(ProxyRouteAccumulator):
        def __init__(
            self,
            path: str | None,
            host: str | None,
            method: str | None,
            status_code: int,
            status_class: StatusClass,
            count: int = 0,
            source_keys: set[str] | None = None,
            first_seen: SnapshotLineReferencePayload | None = None,
            last_seen: SnapshotLineReferencePayload | None = None,
        ) -> None:
            nonlocal created_accumulators
            created_accumulators += 1
            super().__init__(
                path=path,
                host=host,
                method=method,
                status_code=status_code,
                status_class=status_class,
                count=count,
                source_keys=set() if source_keys is None else source_keys,
                first_seen=first_seen,
                last_seen=last_seen,
            )

    monkeypatch.setattr(
        log_analysis,
        "ProxyRouteAccumulator",
        CountingProxyRouteAccumulator,
    )

    analysis = LogAnalysisService()._analyze_proxy_activity(
        sources=[_proxy_source(log_file)],
        max_groups=1,
    )

    assert created_accumulators == 1
    assert len(analysis.top_routes) == 1
    assert analysis.total_route_group_count > 1
