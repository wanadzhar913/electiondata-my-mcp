"""Streamable HTTP OAuth, exercised with an MCP client.

The in-memory ``Client(server)`` skips HTTP, so it never sends ``Authorization``.
These tests mount the ASGI app and connect with ``streamable_http_client``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from electiondata_my_mcp.http_app import build_asgi_app
from electiondata_my_mcp.oauth import (
    ENV_ISSUER_URL,
    ENV_REQUIRED_SCOPES,
    ENV_RESOURCE_URL,
    ENV_TOKENS,
)
from electiondata_my_mcp.server import mcp

pytestmark = pytest.mark.unit

RESOURCE = "http://127.0.0.1:8000/mcp"
METADATA = "/.well-known/oauth-protected-resource/mcp"
ALICE = {"alice-token": {"client_id": "alice", "scopes": ["electiondata:read"]}}
NARROW = {"narrow-token": {"client_id": "narrow", "scopes": ["other"]}}


def _enable(monkeypatch: pytest.MonkeyPatch, tokens: dict[str, dict[str, object]]) -> None:
    monkeypatch.setenv(ENV_ISSUER_URL, "https://auth.example.com")
    monkeypatch.setenv(ENV_RESOURCE_URL, RESOURCE)
    monkeypatch.setenv(ENV_REQUIRED_SCOPES, "electiondata:read")
    monkeypatch.setenv(ENV_TOKENS, json.dumps(tokens))


def _disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ISSUER_URL, raising=False)
    monkeypatch.delenv(ENV_RESOURCE_URL, raising=False)
    monkeypatch.delenv(ENV_REQUIRED_SCOPES, raising=False)
    monkeypatch.delenv(ENV_TOKENS, raising=False)
    build_asgi_app()


@asynccontextmanager
async def _connected(
    app: Starlette,
    token: str | None,
) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx2.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers=headers,
        ) as http_client:
            mcp_transport = streamable_http_client(RESOURCE, http_client=http_client)
            async with Client(mcp_transport, mode="legacy") as client:
                yield client


async def _request(app: Starlette, method: str, path: str, **kwargs: object) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        return await client.request(method, path, **kwargs)


async def test_client_calls_tool_with_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, ALICE)
    try:
        app = build_asgi_app()
        async with _connected(app, "alice-token") as client:
            tools = await client.list_tools()
            assert "validate_sql" in {tool.name for tool in tools.tools}
            result = await client.call_tool(
                "validate_sql",
                {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
            )
        assert result.is_error is False
        assert result.structured_content == {
            "valid": True,
            "tables": ["headline_stats"],
            "errors": [],
            "warnings": [],
        }
    finally:
        _disable(monkeypatch)


def _mcp_error(exc: BaseException) -> MCPError | None:
    if isinstance(exc, MCPError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            found = _mcp_error(inner)
            if found is not None:
                return found
    return None


async def _expect_client_rejected(app: Starlette, token: str | None) -> None:
    # anyio wraps the initialize MCPError in nested ExceptionGroups on the way out.
    with pytest.raises(BaseExceptionGroup) as caught:
        async with _connected(app, token):
            pass
    error = _mcp_error(caught.value)
    assert error is not None
    assert error.message == "Server returned an error response"


async def test_client_without_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, ALICE)
    try:
        app = build_asgi_app()
        await _expect_client_rejected(app, None)
    finally:
        _disable(monkeypatch)


async def test_client_with_insufficient_scope_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, NARROW)
    try:
        app = build_asgi_app()
        denied = await _request(
            app,
            "POST",
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer narrow-token",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert denied.status_code == 403
        assert denied.json()["error"] == "insufficient_scope"
        await _expect_client_rejected(app, "narrow-token")
    finally:
        _disable(monkeypatch)


async def test_missing_token_is_401_and_metadata_names_the_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch, ALICE)
    try:
        app = build_asgi_app()
        denied = await _request(
            app,
            "POST",
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        metadata = await _request(app, "GET", METADATA)
        health = await _request(app, "GET", "/health")
    finally:
        _disable(monkeypatch)

    assert denied.status_code == 401
    www = denied.headers["www-authenticate"]
    assert 'error="invalid_token"' in www
    assert f'resource_metadata="http://127.0.0.1:8000{METADATA}"' in www
    assert denied.json()["error"] == "invalid_token"

    assert metadata.status_code == 200
    body = metadata.json()
    assert body["resource"].rstrip("/") == RESOURCE
    assert body["authorization_servers"] == ["https://auth.example.com/"]
    assert body["scopes_supported"] == ["electiondata:read"]
    assert body["bearer_methods_supported"] == ["header"]

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


async def test_in_memory_client_ignores_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, ALICE)
    try:
        build_asgi_app()
        async with Client(mcp, raise_exceptions=True) as client:
            result = await client.call_tool(
                "validate_sql",
                {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
            )
        assert result.structured_content is not None
        assert result.structured_content["valid"] is True
    finally:
        _disable(monkeypatch)
