"""In-process OAuth 2.1 authorization server for tests.

Discovery, dynamic client registration, ``/authorize`` (PKCE) and ``/token``
are the MCP SDK's own authorization-server routes. ``/jwks`` is the endpoint
a resource server calls to fetch public keys. ``/authorize`` approves every
request as ``alice`` and the token is a JWT whose ``aud`` is the RFC 8707
``resource`` the client asked for.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams, construct_redirect_uri
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from electiondata_my_mcp.oauth import (
    ENV_ISSUER_URL,
    ENV_JWKS_URL,
    ENV_RESOURCE_URL,
)

ISSUER = "https://auth.example.com"
RESOURCE = "http://127.0.0.1:8000/mcp"
SCOPE = "electiondata:read"
JWKS_URL = f"{ISSUER}/jwks"
MCP_ORIGIN = "http://127.0.0.1:8000"

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def resource_server_env() -> dict[str, str]:
    """Resource-server environment for JWKS mode."""
    return {ENV_ISSUER_URL: ISSUER, ENV_RESOURCE_URL: RESOURCE, ENV_JWKS_URL: JWKS_URL}


class FakeAuthorizationServer:
    def __init__(self) -> None:
        self.key = SIGNING_KEY
        self.kid = "key-1"
        self.extra_jwks: list[dict[str, Any]] = []
        self.jwks_fetches = 0
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        routes = create_auth_routes(
            self,
            # AuthSettings keeps ISSUER without the trailing slash AnyHttpUrl adds,
            # matching what the resource server advertises.
            AuthSettings(issuer_url=ISSUER, resource_server_url=None).issuer_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[SCOPE, "other"],
                default_scopes=[SCOPE],
            ),
        )
        self.app = Starlette(
            routes=[
                *routes,
                Route("/jwks", self._jwks),
            ]
        )

    def http_client(self, mcp_app: Starlette | None = None, **kwargs: Any) -> httpx2.AsyncClient:
        """An HTTP client that reaches this server and, optionally, the MCP app."""
        mounts = {ISSUER: httpx2.ASGITransport(app=self.app)}
        if mcp_app is not None:
            mounts[MCP_ORIGIN] = httpx2.ASGITransport(app=mcp_app)
        return httpx2.AsyncClient(mounts=mounts, **kwargs)

    def mint(
        self,
        *,
        key: Any = None,
        algorithm: str = "RS256",
        headers: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> str:
        """Sign an access token. An override of ``None`` drops that claim."""
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": RESOURCE,
            "sub": "alice",
            "client_id": "test-client",
            "scope": SCOPE,
            "iat": now,
            "exp": now + 3600,
            **overrides,
        }
        return jwt.encode(
            {name: value for name, value in claims.items() if value is not None},
            self.key if key is None else key,
            algorithm=algorithm,
            headers={"kid": self.kid, **(headers or {})},
        )

    def rotate(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = f"key-{secrets.token_hex(4)}"

    async def _jwks(self, _request: Request) -> JSONResponse:
        self.jwks_fetches += 1
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=self.kid, use="sig", alg="RS256")
        return JSONResponse({"keys": [jwk, *self.extra_jwks]})

    # OAuthAuthorizationServerProvider, as far as create_auth_routes uses it.

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        assert client.client_id is not None
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="alice",
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, _client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.codes[authorization_code.code]
        scope = " ".join(authorization_code.scopes)
        token = self.mint(
            aud=authorization_code.resource,
            client_id=client.client_id,
            sub=authorization_code.subject,
            scope=scope,
        )
        return OAuthToken(access_token=token, token_type="Bearer", expires_in=3600, scope=scope)

    async def load_refresh_token(self, _client: OAuthClientInformationFull, _token: str) -> None:
        return None

    async def load_access_token(self, _token: str) -> None:
        return None
