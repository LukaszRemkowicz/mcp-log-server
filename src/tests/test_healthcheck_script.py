from __future__ import annotations

import urllib.request

from pytest import MonkeyPatch

import healthcheck


class _Response:
    def read(self) -> bytes:
        return b'{"status":"ok"}'


def test_build_healthcheck_url_uses_mcp_port(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PORT", "9001")

    assert healthcheck.build_healthcheck_url() == "http://127.0.0.1:9001/healthz"


def test_main_calls_healthz(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_urlopen(url: str, *, timeout: int) -> _Response:
        calls.append((url, timeout))
        return _Response()

    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    healthcheck.main()

    assert calls == [("http://127.0.0.1:8001/healthz", 5)]
