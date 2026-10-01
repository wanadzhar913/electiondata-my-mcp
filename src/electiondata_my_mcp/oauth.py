"""Opt-in OAuth 2.1 resource server for Streamable HTTP.

The process never issues tokens. When ``MCP_OAUTH_ISSUER_URL`` is set, ``/mcp``
requires an access token minted by that authorization server. JWT access tokens
are checked against its JWKS (``MCP_OAUTH_JWKS_URL``). ``GET /health`` and the
RFC 9728 metadata stay open. Stdio and the in-memory test client never see the
header, so they stay open too — same rule as the MCP SDK.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx2
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

logger = logging.getLogger(__name__)

ENV_ISSUER_URL = "MCP_OAUTH_ISSUER_URL"
ENV_RESOURCE_URL = "MCP_OAUTH_RESOURCE_URL"
ENV_REQUIRED_SCOPES = "MCP_OAUTH_REQUIRED_SCOPES"
ENV_AUDIENCE = "MCP_OAUTH_AUDIENCE"
ENV_JWKS_URL = "MCP_OAUTH_JWKS_URL"
_ENV_NAMES = (
    ENV_ISSUER_URL,
    ENV_RESOURCE_URL,
    ENV_REQUIRED_SCOPES,
    ENV_AUDIENCE,
    ENV_JWKS_URL,
)

DEFAULT_REQUIRED_SCOPES = ("electiondata:read",)
HTTP_TIMEOUT = httpx2.Timeout(10.0, connect=5.0)
JWKS_CACHE_SECONDS = 3600.0
JWKS_RETRY_SECONDS = 30.0
JWT_LEEWAY_SECONDS = 30
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(timeout=HTTP_TIMEOUT)


def _same_url(a: str, b: str) -> bool:
    return a.removesuffix("/") == b.removesuffix("/")


def _access_token(
    token: str,
    claims: dict[str, Any],
    *,
    issuer: str,
    audience: str,
) -> AccessToken | None:
    """Map JWT or RFC 7662 claims to an ``AccessToken``, or ``None`` if the token
    was not issued by ``issuer`` for ``audience``."""
    if claims.get("iss", issuer) != issuer:
        return None
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    resource = next((a for a in audiences if isinstance(a, str) and _same_url(a, audience)), None)
    if resource is None:
        return None
    # RFC 9068 / 7662 ``scope`` is a space-separated string; Entra and Okta
    # put a string or a list in ``scp``.
    scope = claims.get("scope") or claims.get("scp")
    if isinstance(scope, str):
        scopes = scope.split()
    elif isinstance(scope, list):
        scopes = [s for s in scope if isinstance(s, str)]
    else:
        scopes = []
    try:
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or "unknown"),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=resource,
            subject=claims.get("sub"),
            claims=claims,
        )
    except ValidationError:
        return None


class JWTTokenVerifier:
    """Verify JWT access tokens against the authorization server's JWKS.

    Only asymmetric keys from the JWKS are used, and a token must be signed with
    the algorithm of the key its ``kid`` names, so ``alg: none`` and HMAC tokens
    are refused. Keys are cached for ``JWKS_CACHE_SECONDS``; an unknown ``kid``
    refetches at most once per ``JWKS_RETRY_SECONDS`` to pick up key rotation.
    """

    def __init__(self, jwks_url: str, *, issuer: str, audience: str) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._keys: dict[str | None, jwt.PyJWK] = {}
        self._refresh_at = 0.0
        self._retry_at = 0.0
        self._lock = anyio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await self._signing_key(jwt.get_unverified_header(token).get("kid"))
            if key is None:
                return None
            claims = jwt.decode(
                token,
                key,
                algorithms=[key.algorithm_name],
                issuer=self._issuer,
                leeway=JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud"], "verify_aud": False},
            )
        except jwt.PyJWTError as error:
            logger.info("Rejected JWT access token: %s", error)
            return None
        return _access_token(token, claims, issuer=self._issuer, audience=self._audience)

    def _needs_refresh(self, kid: str | None) -> bool:
        now = time.monotonic()
        return now >= self._refresh_at or (kid not in self._keys and now >= self._retry_at)

    async def _signing_key(self, kid: str | None) -> jwt.PyJWK | None:
        if self._needs_refresh(kid):
            async with self._lock:
                if self._needs_refresh(kid):
                    await self._refresh()
        return self._keys.get(kid)

    async def _refresh(self) -> None:
        now = time.monotonic()
        self._refresh_at = self._retry_at = now + JWKS_RETRY_SECONDS
        try:
            async with _http_client() as client:
                response = await client.get(self._jwks_url)
            response.raise_for_status()
            body = response.json()
            jwks = jwt.PyJWKSet(body.get("keys") if isinstance(body, dict) else None)
        except (httpx2.HTTPError, ValueError, jwt.PyJWTError) as error:
            logger.warning("Could not fetch JWKS from %s: %s", self._jwks_url, error)
            return
        self._keys = {
            key.key_id: key
            for key in jwks.keys
            if key.key_type != "oct" and key.public_key_use in (None, "sig")
        }
        self._refresh_at = now + JWKS_CACHE_SECONDS


@dataclass(frozen=True)
class OAuthConfig:
    auth: AuthSettings
    verifier: TokenVerifier


def load_oauth_config() -> OAuthConfig | None:
    """Read OAuth settings from the environment.

    ``None`` means HTTP stays unauthenticated. A half-configured environment
    raises ``ValueError`` so the process does not boot looking protected when
    it is not. Error messages name variables, never their values.
    """
    env = {name: os.environ.get(name, "").strip() for name in _ENV_NAMES}
    issuer = env[ENV_ISSUER_URL]
    if not issuer:
        stray = next((name for name in _ENV_NAMES if env[name]), None)
        if stray:
            raise ValueError(f"{stray} is set but {ENV_ISSUER_URL} is not")
        return None

    resource = env[ENV_RESOURCE_URL]
    if not resource:
        raise ValueError(f"{ENV_RESOURCE_URL} is required when {ENV_ISSUER_URL} is set")
    jwks_url = env[ENV_JWKS_URL]
    if not jwks_url:
        raise ValueError(f"{ENV_JWKS_URL} is required when {ENV_ISSUER_URL} is set")
    for name in (ENV_ISSUER_URL, ENV_RESOURCE_URL, ENV_JWKS_URL):
        if env[name]:
            _require_https(name, env[name])

    audience = env[ENV_AUDIENCE] or resource
    verifier: TokenVerifier = JWTTokenVerifier(jwks_url, issuer=issuer, audience=audience)

    auth = AuthSettings.model_validate(
        {
            # Strings, not AnyHttpUrl: AuthSettings keeps a path-less issuer
            # without a trailing slash, and clients compare it to the
            # authorization server's metadata ``issuer`` exactly.
            "issuer_url": issuer,
            "resource_server_url": resource,
            "required_scopes": _parse_scopes(env[ENV_REQUIRED_SCOPES]),
            # A custom audience (Auth0 API identifier, Entra app ID) is checked
            # by the verifier instead of the SDK's resource match.
            "validate_token_resource": _same_url(audience, resource),
        }
    )
    return OAuthConfig(auth=auth, verifier=verifier)


def apply_oauth_config(server: MCPServer[Any]) -> OAuthConfig | None:
    """Point ``server`` at the current process environment.

    ``streamable_http_app()`` reads ``settings.auth`` and ``_token_verifier``
    when the ASGI app is built. The SDK has no public setter for either after
    construction, and ``server`` is the module-level instance the tools are
    registered on. Each uvicorn worker imports the app in its own process, so
    this is the whole configuration step.
    """
    config = load_oauth_config()
    server.settings.auth = config.auth if config else None
    server._token_verifier = config.verifier if config else None
    return config


def _require_https(name: str, raw: str) -> None:
    url = urlsplit(raw)
    loopback = url.scheme == "http" and url.hostname in _LOOPBACK_HOSTS
    if not url.hostname or not (url.scheme == "https" or loopback):
        raise ValueError(f"{name} must be an https URL (http only for localhost)")


def _parse_scopes(raw: str) -> list[str]:
    if not raw:
        return list(DEFAULT_REQUIRED_SCOPES)
    scopes = [part.strip() for part in raw.split(",") if part.strip()]
    if not scopes:
        raise ValueError(f"{ENV_REQUIRED_SCOPES} must list at least one scope")
    return scopes
