from __future__ import annotations

import httpx
import pytest

from app import create_application, mcp
from conf import settings
from tests.conftest import _test_lifespan_without_database

pytestmark = pytest.mark.anyio


async def test_healthz_returns_ok_without_authentication() -> None:
    app = create_application(auth_provider=None)
    asgi_app = app.http_app(
        path=settings.MCP_PATH,
        json_response=settings.MCP_JSON_RESPONSE,
        stateless_http=settings.MCP_STATELESS_HTTP,
    )
    previous_lifespan = mcp._lifespan  # noqa: SLF001
    mcp._lifespan = _test_lifespan_without_database  # noqa: SLF001
    try:
        async with asgi_app.router.lifespan_context(asgi_app):
            transport = httpx.ASGITransport(app=asgi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.get("/healthz")
    finally:
        mcp._lifespan = previous_lifespan  # noqa: SLF001

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
