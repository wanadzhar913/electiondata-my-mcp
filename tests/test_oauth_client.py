"""Streamable HTTP OAuth, end to end.

The in-memory ``Client(server)`` skips HTTP, so it never sends ``Authorization``.
These tests mount the ASGI app next to a fake authorization server and connect
with the SDK's ``OAuthClientProvider``: 401, protected-resource metadata,
authorization-server metadata, dynamic registration, PKCE, token, retry.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl
from starlette.applications import Starlette

from electiondata_my_mcp.http_app import build_asgi_app
from electiondata_my_mcp.oauth import ENV_ISSUER_URL
from electiondata_my_mcp.server import mcp
from fake_authorization_server import (
    ISSUER,
    MCP_ORIGIN,
    OTHER_KEY,
    RESOURCE,
    SCOPE,
    FakeAuthorizationServer,
    resource_server_env,
)

pytestmark = pytest.mark.unit

METADATA = "/.well-known/oauth-protected-resource/mcp"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class InMemoryTokenStorage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


@pytest.fixture
def oauth_app(
    oauth_env: pytest.MonkeyPatch,
    auth_server: FakeAuthorizationServer,
) -> Starlette:
    for name, value in resource_server_env().items():
        oauth_env.setenv(name, value)
    return build_asgi_app()


async def test_oauth_client_provider_signs_in_and_calls_a_tool(
    oauth_app: Starlette, auth_server: FakeAuthorizationServer
) -> None:
    redirects: list[str] = []

    async def open_browser(authorization_url: str) -> None:
        async with auth_server.http_client() as browser:
            response = await browser.get(authorization_url)
        assert response.status_code == 302
        redirects.append(response.headers["location"])

    async def wait_for_callback() -> AuthorizationCodeResult:
        params = parse_qs(urlparse(redirects[-1]).query)
        return AuthorizationCodeResult(
            code=params["code"][0],
            state=params["state"][0],
            iss=params["iss"][0] if "iss" in params else None,
        )

    storage = InMemoryTokenStorage()
    provider = OAuthClientProvider(
        server_url=RESOURCE,
        client_metadata=OAuthClientMetadata(
            client_name="ElectionData.MY test client",
            redirect_uris=[AnyUrl("http://localhost:3030/callback")],
            scope=SCOPE,
        ),
        storage=storage,
        redirect_handler=open_browser,
        callback_handler=wait_for_callback,
    )

    async with oauth_app.router.lifespan_context(oauth_app):
        async with auth_server.http_client(oauth_app, auth=provider) as http_client:
            transport = streamable_http_client(RESOURCE, http_client=http_client)
            async with Client(transport, mode="legacy") as client:
                result = await client.call_tool(
                    "validate_sql",
                    {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
                )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["valid"] is True
    assert len(redirects) == 1
    assert list(auth_server.clients) == [
        storage.client_info.client_id if storage.client_info else None
    ]
    assert storage.tokens is not None
    claims = jwt.decode(storage.tokens.access_token, options={"verify_signature": False})
    # The client asked for this resource (RFC 8707) and the token is bound to it.
    assert claims["aud"] == RESOURCE
    assert claims["scope"] == SCOPE


async def test_missing_token_is_401_and_metadata_names_the_issuer(
    oauth_app: Starlette, auth_server: FakeAuthorizationServer
) -> None:
    async with auth_server.http_client(oauth_app, base_url=MCP_ORIGIN) as http:
        denied = [
            await http.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE),
            await http.get("/mcp", headers=MCP_HEADERS),
            await http.delete("/mcp"),
        ]
        metadata = await http.get(METADATA)
        health = await http.get("/health")

    for response in denied:
        assert response.status_code == 401
        www = response.headers["www-authenticate"]
        assert 'error="invalid_token"' in www
        assert f'resource_metadata="{MCP_ORIGIN}{METADATA}"' in www

    assert metadata.status_code == 200
    assert metadata.json() == {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
    }
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("claims", "status", "error"),
    [
        ({}, 200, None),
        ({"key": OTHER_KEY}, 401, "invalid_token"),
        ({"aud": "https://other.example.com/mcp"}, 401, "invalid_token"),
        ({"exp": 1}, 401, "invalid_token"),
        ({"scope": "other"}, 403, "insufficient_scope"),
    ],
    ids=["valid", "forged", "other audience", "expired", "missing scope"],
)
async def test_bearer_token_checks(
    oauth_app: Starlette,
    auth_server: FakeAuthorizationServer,
    claims: dict[str, object],
    status: int,
    error: str | None,
) -> None:
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {auth_server.mint(**claims)}"}

    async with oauth_app.router.lifespan_context(oauth_app):
        async with auth_server.http_client(oauth_app, base_url=MCP_ORIGIN) as http:
            response = await http.post("/mcp", headers=headers, json=INITIALIZE)

    assert response.status_code == status
    if error is not None:
        assert response.json()["error"] == error


async def test_in_memory_client_ignores_oauth(oauth_app: Starlette) -> None:
    async with Client(mcp, raise_exceptions=True) as client:
        result = await client.call_tool(
            "validate_sql",
            {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
        )
    assert result.structured_content is not None
    assert result.structured_content["valid"] is True


def test_half_configured_oauth_fails_to_build_the_app(oauth_env: pytest.MonkeyPatch) -> None:
    oauth_env.setenv(ENV_ISSUER_URL, ISSUER)
    with pytest.raises(ValueError, match="MCP_OAUTH_RESOURCE_URL is required"):
        build_asgi_app()
