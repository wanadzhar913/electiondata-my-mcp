"""Opt-in OAuth 2.1 resource server for Streamable HTTP.

The process never issues tokens. When ``MCP_OAUTH_ISSUER_URL`` is set, ``/mcp``
requires a bearer token listed in ``MCP_OAUTH_TOKENS``. ``GET /health`` stays
open. Stdio and the in-memory test client never see the header, so they stay
open too — same rule as the MCP SDK.

``ponytail:`` the verifier is a static table, not JWT signature checks or
RFC 7662 introspection. Swap ``StaticTokenVerifier`` for one of those when
tokens are minted by the advertised issuer instead of pasted into the env.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import validate_issuer_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl, ValidationError

ENV_ISSUER_URL = "MCP_OAUTH_ISSUER_URL"
ENV_RESOURCE_URL = "MCP_OAUTH_RESOURCE_URL"
ENV_REQUIRED_SCOPES = "MCP_OAUTH_REQUIRED_SCOPES"
ENV_TOKENS = "MCP_OAUTH_TOKENS"

DEFAULT_RESOURCE_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_REQUIRED_SCOPES = ("electiondata:read",)


class StaticTokenVerifier:
    """Look a bearer token up in a fixed table. Unknown tokens return ``None``."""

    def __init__(self, tokens: dict[str, AccessToken]) -> None:
        self._tokens = tokens

    async def verify_token(self, token: str) -> AccessToken | None:
        return self._tokens.get(token)


@dataclass(frozen=True)
class OAuthConfig:
    auth: AuthSettings
    verifier: StaticTokenVerifier


def load_oauth_config() -> OAuthConfig | None:
    """Read OAuth settings from the environment.

    ``None`` means HTTP stays unauthenticated. A half-configured environment
    (issuer without tokens, or tokens without an issuer) raises ``ValueError``
    so the process does not boot looking protected when it is not, or the
    reverse.
    """
    issuer_raw = os.environ.get(ENV_ISSUER_URL, "").strip()
    tokens_raw = os.environ.get(ENV_TOKENS)
    if not issuer_raw:
        if tokens_raw is not None and tokens_raw.strip():
            raise ValueError(f"{ENV_TOKENS} is set but {ENV_ISSUER_URL} is not")
        return None

    resource_raw = os.environ.get(ENV_RESOURCE_URL, "").strip() or DEFAULT_RESOURCE_URL
    scopes = _parse_scopes(os.environ.get(ENV_REQUIRED_SCOPES))
    try:
        issuer_url = AnyHttpUrl(issuer_raw)
        resource_url = AnyHttpUrl(resource_raw)
    except ValidationError as error:
        raise ValueError(f"Invalid OAuth URL: {error}") from error
    validate_issuer_url(issuer_url)

    tokens = _parse_tokens(tokens_raw, resource=str(resource_url), default_scopes=scopes)
    return OAuthConfig(
        auth=AuthSettings(
            issuer_url=issuer_url,
            resource_server_url=resource_url,
            required_scopes=scopes,
        ),
        verifier=StaticTokenVerifier(tokens),
    )


def apply_oauth_config(server: MCPServer[Any]) -> OAuthConfig | None:
    """Point ``server`` at the current process environment.

    ``streamable_http_app()`` reads ``settings.auth`` and ``_token_verifier``
    when the ASGI app is built. Each uvicorn worker imports the app in its own
    process, so this is the whole configuration step.
    """
    config = load_oauth_config()
    if config is None:
        server.settings.auth = None
        server._token_verifier = None
        return None
    server.settings.auth = config.auth
    server._token_verifier = config.verifier
    return config


def _parse_scopes(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return list(DEFAULT_REQUIRED_SCOPES)
    scopes = [part.strip() for part in raw.split(",") if part.strip()]
    if not scopes:
        raise ValueError(f"{ENV_REQUIRED_SCOPES} must list at least one scope")
    return scopes


def _parse_tokens(
    raw: str | None,
    *,
    resource: str,
    default_scopes: list[str],
) -> dict[str, AccessToken]:
    if raw is None or not raw.strip():
        raise ValueError(f"{ENV_ISSUER_URL} is set but {ENV_TOKENS} is empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{ENV_TOKENS} must be a JSON object") from error
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(f"{ENV_TOKENS} must be a non-empty JSON object")

    tokens: dict[str, AccessToken] = {}
    for token, entry in parsed.items():
        if not isinstance(token, str) or not token:
            raise ValueError(f"{ENV_TOKENS} keys must be non-empty token strings")
        if not isinstance(entry, dict):
            raise ValueError(f"{ENV_TOKENS}[{token!r}] must be an object with client_id")
        client_id = entry.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise ValueError(f"{ENV_TOKENS}[{token!r}] needs a client_id string")
        scopes = _token_scopes(token, entry.get("scopes", default_scopes))
        subject = entry.get("subject")
        if subject is not None and not isinstance(subject, str):
            raise ValueError(f"{ENV_TOKENS}[{token!r}] subject must be a string")
        tokens[token] = AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            resource=resource,
            subject=subject,
        )
    return tokens


def _token_scopes(token: str, scopes: object) -> list[str]:
    if (
        not isinstance(scopes, list)
        or not scopes
        or not all(isinstance(scope, str) and scope for scope in scopes)
    ):
        raise ValueError(f"{ENV_TOKENS}[{token!r}] scopes must be a non-empty list of strings")
    return list(scopes)
