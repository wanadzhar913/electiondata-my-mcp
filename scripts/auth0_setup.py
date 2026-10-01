"""Provision Auth0 for the ElectionData.MY MCP resource server (example).

This MCP server only **verifies** JWT access tokens (JWKS); Auth0 **issues** them.
Use this script as a worked example of wiring Auth0 to the same settings as
``tests/test_live_auth0_oauth.py`` and ``docs/auth0.md``:

  1. Auth0 API whose identifier is ``MCP_OAUTH_RESOURCE_URL`` (RS256,
     ``electiondata:read`` scope).
  2. Machine-to-machine application authorized for that API (for CI and
     client-credentials tests).

How it works:

  - Performs Auth0's device authorization flow (same public client as
    ``@auth0/auth0-mcp-server``) to obtain a short-lived Management API token.
  - Spawns ``npx @auth0/auth0-mcp-server run`` with that token in ``AUTH0_TOKEN``
    so Management API tools can create or reuse the API, M2M app, and grant.
  - Idempotent: re-running prints "already exists" when objects are present.

When to use it:

  - Bootstrapping a tenant for local or CI live OAuth tests without manual
    dashboard steps.
  - Learning how Auth0 maps to ``MCP_OAUTH_ISSUER_URL``, ``MCP_OAUTH_JWKS_URL``,
    and ``MCP_OAUTH_RESOURCE_URL``.

When not to use it:

  - Production deploys (use Terraform, Auth0 Deploy CLI, or your own IaC).
  - End-user browser login (that needs a separate SPA or regular web application).

Requires: ``uv sync --group dev``, Node.js, network access. See ``docs/auth0.md``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
import webbrowser
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.stdio import StdioServerParameters

# Same device-flow client as @auth0/auth0-mcp-server (public, not a secret).
LOGIN_TENANT = "auth0.auth0.com"
DEFAULT_DEVICE_CLIENT_ID = "2lhnuYMRQ8IpR5hNsOhDFQqrGQUQMRm5"
LOGIN_CLIENT_ID = os.environ.get("AUTH0_MCP_DEVICE_CLIENT_ID", DEFAULT_DEVICE_CLIENT_ID)
SCOPES = (
    "read:resource_servers create:resource_servers read:clients create:clients create:client_grants"
)

API_IDENTIFIER = "http://127.0.0.1:8000/mcp"
API_NAME = "ElectionData.MY MCP"
APP_NAME = "electiondata-my-mcp CI"
SCOPE = "electiondata:read"
TOOLS = [
    "auth0_list_resource_servers",
    "auth0_create_resource_server",
    "auth0_list_applications",
    "auth0_create_application",
    "auth0_create_application_grant",
]


def open_verification_url(url: str) -> None:
    if shutil.which("wslview"):
        subprocess.run(["wslview", url], check=False)
    else:
        webbrowser.open(url)


def device_login() -> tuple[str, str]:
    if not LOGIN_CLIENT_ID:
        raise SystemExit(
            "Set AUTH0_MCP_DEVICE_CLIENT_ID or use the default from @auth0/auth0-mcp-server."
        )
    with httpx2.Client(timeout=30) as http:
        code = http.post(
            f"https://{LOGIN_TENANT}/oauth/device/code",
            data={
                "client_id": LOGIN_CLIENT_ID,
                "audience": "https://*.auth0.com/api/v2/",
                "scope": SCOPES,
            },
        ).json()
        if "user_code" not in code:
            raise SystemExit(f"Device flow failed: {code}")
        print(f"DEVICE CODE: {code['user_code']}", flush=True)
        print(f"APPROVE AT: {code['verification_uri_complete']}", flush=True)
        open_verification_url(code["verification_uri_complete"])
        while True:
            time.sleep(code.get("interval", 5))
            token = http.post(
                f"https://{LOGIN_TENANT}/oauth/token",
                data={
                    "client_id": LOGIN_CLIENT_ID,
                    "device_code": code["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            ).json()
            if "access_token" in token:
                break
            if token.get("error") not in ("authorization_pending", "slow_down"):
                raise SystemExit(f"Login failed: {token.get('error')}")
    access_token = token["access_token"]
    payload = access_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    domain = next(urlsplit(a).netloc for a in audiences if urlsplit(a).path == "/api/v2/")
    return access_token, domain


def text_of(result) -> str:
    if result.is_error:
        raise SystemExit(
            "Tool error: " + " ".join(c.text for c in result.content if hasattr(c, "text"))
        )
    return "".join(c.text for c in result.content if hasattr(c, "text"))


def as_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def main() -> None:
    token, domain = device_login()
    print(f"Signed in to tenant: {domain}", flush=True)

    params = StdioServerParameters(
        command="npx",
        args=["-y", "@auth0/auth0-mcp-server", "run", "--tools", ",".join(TOOLS)],
        env={
            **os.environ,
            "AUTH0_TOKEN": token,
            "AUTH0_DOMAIN": domain,
            "AUTH0_MCP_ANALYTICS": "false",
        },
    )
    async with Client(params, read_timeout_seconds=90) as client:
        apis = text_of(await client.call_tool("auth0_list_resource_servers", {"per_page": 100}))
        if API_IDENTIFIER in apis:
            print(f"API already exists: {API_IDENTIFIER}", flush=True)
        else:
            text_of(
                await client.call_tool(
                    "auth0_create_resource_server",
                    {
                        "name": API_NAME,
                        "identifier": API_IDENTIFIER,
                        "signing_alg": "RS256",
                        "scopes": [
                            {"value": SCOPE, "description": "Query the ElectionData.MY lake"}
                        ],
                    },
                )
            )
            print(f"Created API: {API_NAME} ({API_IDENTIFIER})", flush=True)

        apps = as_json(
            text_of(await client.call_tool("auth0_list_applications", {"per_page": 100}))
        )
        app_list = apps.get("applications", []) if isinstance(apps, dict) else (apps or [])
        existing = next((a for a in app_list if a.get("name") == APP_NAME), None)
        if existing:
            client_id = existing["client_id"]
            print(f"App already exists: {APP_NAME} (client_id {client_id})", flush=True)
        else:
            created = as_json(
                text_of(
                    await client.call_tool(
                        "auth0_create_application",
                        {
                            "name": APP_NAME,
                            "app_type": "non_interactive",
                            "description": "CI: live Auth0 OAuth test for electiondata-my-mcp",
                        },
                    )
                )
            )
            app = created.get("application", created) if isinstance(created, dict) else {}
            client_id = app.get("client_id")
            if not client_id:
                raise SystemExit(f"Could not read client_id; response keys: {list(created or {})}")
            print(f"Created app: {APP_NAME} (client_id {client_id})", flush=True)

        grant = await client.call_tool(
            "auth0_create_application_grant",
            {"client_id": client_id, "audience": API_IDENTIFIER, "scope": [SCOPE]},
        )
        if (
            grant.is_error
            and "already exists"
            in " ".join(c.text for c in grant.content if hasattr(c, "text")).lower()
        ):
            print("Grant already exists", flush=True)
        else:
            text_of(grant)
            print(f"Granted {SCOPE} on {API_IDENTIFIER} to {APP_NAME}", flush=True)

    print(f"DONE tenant={domain} client_id={client_id}", flush=True)
    print(
        "Next: set MCP_OAUTH_* on the HTTP server (see docs/auth0.md) and "
        "AUTH0_DOMAIN / AUTH0_CI_CLIENT_ID / AUTH0_CI_CLIENT_SECRET for tests.",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
