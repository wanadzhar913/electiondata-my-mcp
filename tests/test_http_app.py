from __future__ import annotations

import httpx
import pytest

from electiondata_my_mcp.http_app import app, build_asgi_app
from electiondata_my_mcp.server import mcp
from electiondata_my_mcp.settings import ENV_ALLOWED_HOSTS, ENV_ALLOWED_ORIGINS, ENV_DNS_REBINDING

pytestmark = pytest.mark.unit

async def _request(asgi_app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


async def test_health_route_returns_ok() -> None:
    response = await _request(app, "GET", "/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_lifespan_starts_session_manager() -> None:
    built = build_asgi_app()
    async with built.router.lifespan_context(built):
        assert mcp.session_manager is not None


async def test_http_transport_is_stateless() -> None:
    built = build_asgi_app()
    transport = httpx.ASGITransport(app=built)
    async with built.router.lifespan_context(built):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost:8000",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1"},
                    },
                },
            )

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers


async def test_cors_preflight_and_expose_session_header() -> None:
    preflight = await _request(
        app,
        "OPTIONS",
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,mcp-session-id",
        },
    )
    response = await _request(
        app,
        "GET",
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    allow_headers = preflight.headers["access-control-allow-headers"].lower()
    assert "mcp-session-id" in allow_headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    expose = response.headers["access-control-expose-headers"].lower()
    assert "mcp-session-id" in expose


def test_transport_security_follows_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ALLOWED_HOSTS, "mcp.example.com,mcp.example.com:*")
    monkeypatch.setenv(ENV_ALLOWED_ORIGINS, "https://app.example.com")
    monkeypatch.setenv(ENV_DNS_REBINDING, "true")

    built = build_asgi_app()
    security = built.state.transport_security
    assert security.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]
    assert security.allowed_origins == ["https://app.example.com"]
    assert security.enable_dns_rebinding_protection is True


def test_dns_rebinding_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DNS_REBINDING, "false")
    built = build_asgi_app()
    assert built.state.transport_security.enable_dns_rebinding_protection is False
