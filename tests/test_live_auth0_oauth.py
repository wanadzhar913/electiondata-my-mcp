"""Streamable HTTP OAuth against a real Auth0 tenant.

The fake authorization server covers the protocol. This covers what only a
real tenant can: Auth0's issuer (trailing ``/``), its JWKS, and the ``aud`` and
``scope`` claims it puts in a client-credentials token. The tenant needs an API
whose identifier is ``RESOURCE`` with the ``electiondata:read`` scope, and an
M2M application authorized for it.
"""

from __future__ import annotations

import os

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from electiondata_my_mcp.http_app import build_asgi_app
from electiondata_my_mcp.oauth import ENV_ISSUER_URL, ENV_JWKS_URL, ENV_RESOURCE_URL
from fake_authorization_server import MCP_ORIGIN, RESOURCE

pytestmark = pytest.mark.integration

DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
CLIENT_ID = os.environ.get("AUTH0_CI_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AUTH0_CI_CLIENT_SECRET", "")


@pytest.mark.skipif(
    not (DOMAIN and CLIENT_ID and CLIENT_SECRET),
    reason="AUTH0_DOMAIN, AUTH0_CI_CLIENT_ID and AUTH0_CI_CLIENT_SECRET are not set",
)
async def test_auth0_access_token_calls_a_tool(oauth_env: pytest.MonkeyPatch) -> None:
    oauth_env.setenv(ENV_ISSUER_URL, f"https://{DOMAIN}/")
    oauth_env.setenv(ENV_RESOURCE_URL, RESOURCE)
    oauth_env.setenv(ENV_JWKS_URL, f"https://{DOMAIN}/.well-known/jwks.json")

    async with httpx2.AsyncClient(timeout=30.0) as auth0:
        response = await auth0.post(
            f"https://{DOMAIN}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "audience": RESOURCE,
            },
        )
    response.raise_for_status()
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    app = build_asgi_app()
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=MCP_ORIGIN, headers=headers
        ) as http_client:
            transport = streamable_http_client(RESOURCE, http_client=http_client)
            async with Client(transport, mode="legacy") as client:
                result = await client.call_tool(
                    "validate_sql",
                    {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
                )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["valid"] is True
