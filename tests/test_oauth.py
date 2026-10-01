from __future__ import annotations

import base64
import time
from collections.abc import Callable

import pytest

from electiondata_my_mcp import oauth
from electiondata_my_mcp.oauth import (
    ENV_AUDIENCE,
    ENV_ISSUER_URL,
    ENV_JWKS_URL,
    ENV_REQUIRED_SCOPES,
    ENV_RESOURCE_URL,
    JWTTokenVerifier,
    load_oauth_config,
)
from fake_authorization_server import (
    ISSUER,
    JWKS_URL,
    OTHER_KEY,
    RESOURCE,
    SCOPE,
    FakeAuthorizationServer,
    resource_server_env,
)

pytestmark = pytest.mark.unit

JWKS_ENV = resource_server_env()


def _setenv(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def _jwt_verifier(audience: str = RESOURCE) -> JWTTokenVerifier:
    return JWTTokenVerifier(JWKS_URL, issuer=ISSUER, audience=audience)


def test_oauth_is_off_by_default(oauth_env: pytest.MonkeyPatch) -> None:
    assert load_oauth_config() is None


def test_jwks_config(oauth_env: pytest.MonkeyPatch) -> None:
    _setenv(oauth_env, {**JWKS_ENV, ENV_REQUIRED_SCOPES: "electiondata:read, extra"})

    config = load_oauth_config()

    assert config is not None
    assert isinstance(config.verifier, JWTTokenVerifier)
    # Exact string: clients compare it to the authorization server's ``issuer``.
    assert str(config.auth.issuer_url) == ISSUER
    assert str(config.auth.resource_server_url) == RESOURCE
    assert config.auth.required_scopes == ["electiondata:read", "extra"]
    assert config.auth.validate_token_resource is True


def test_custom_audience_is_checked_by_the_verifier(oauth_env: pytest.MonkeyPatch) -> None:
    _setenv(oauth_env, {**JWKS_ENV, ENV_AUDIENCE: "api://electiondata"})

    config = load_oauth_config()

    assert config is not None
    assert config.auth.validate_token_resource is False


def test_apply_oauth_config_sets_sdk_token_verifier_attribute() -> None:
    """Guard test: fail loudly if the SDK stops using _token_verifier."""
    from electiondata_my_mcp.server import mcp

    assert hasattr(mcp, "_token_verifier"), (
        "MCPServer._token_verifier attribute no longer exists. "
        "Update apply_oauth_config to use the SDK's public API for token verification."
    )


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({ENV_JWKS_URL: JWKS_URL}, "MCP_OAUTH_JWKS_URL is set but MCP_OAUTH_ISSUER_URL is not"),
        ({ENV_ISSUER_URL: ISSUER, ENV_JWKS_URL: JWKS_URL}, "MCP_OAUTH_RESOURCE_URL is required"),
        ({ENV_ISSUER_URL: ISSUER, ENV_RESOURCE_URL: RESOURCE}, "MCP_OAUTH_JWKS_URL is required"),
        ({**JWKS_ENV, ENV_ISSUER_URL: "http://auth.example.com"}, "MCP_OAUTH_ISSUER_URL must"),
        (
            {**JWKS_ENV, ENV_RESOURCE_URL: "http://mcp.example.com/mcp"},
            "MCP_OAUTH_RESOURCE_URL must",
        ),
        ({**JWKS_ENV, ENV_RESOURCE_URL: "not a url"}, "MCP_OAUTH_RESOURCE_URL must"),
        ({**JWKS_ENV, ENV_JWKS_URL: "http://auth.example.com/jwks"}, "MCP_OAUTH_JWKS_URL must"),
        ({**JWKS_ENV, ENV_REQUIRED_SCOPES: ","}, "at least one scope"),
    ],
)
def test_bad_env_fails_at_startup_without_echoing_values(
    oauth_env: pytest.MonkeyPatch, env: dict[str, str], match: str
) -> None:
    _setenv(oauth_env, env)

    with pytest.raises(ValueError, match=match) as caught:
        load_oauth_config()

    assert not any(value and value in str(caught.value) for value in env.values())


async def test_jwt_verifier_accepts_token_from_issuer(auth_server: FakeAuthorizationServer) -> None:
    token = auth_server.mint()

    access = await _jwt_verifier().verify_token(token)

    assert access is not None
    assert access.client_id == "test-client"
    assert access.scopes == [SCOPE]
    assert access.subject == "alice"
    assert access.resource == RESOURCE
    assert access.expires_at is not None and access.expires_at > time.time()
    assert access.claims is not None and access.claims["iss"] == ISSUER


async def test_jwt_verifier_reads_scp_azp_and_audience_lists(
    auth_server: FakeAuthorizationServer,
) -> None:
    token = auth_server.mint(
        aud=["https://graph.example.com", "api://electiondata"],
        scope=None,
        scp=[SCOPE],
        client_id=None,
        azp="entra-app",
    )

    access = await _jwt_verifier(audience="api://electiondata").verify_token(token)

    assert access is not None
    assert access.client_id == "entra-app"
    assert access.scopes == [SCOPE]
    assert access.resource == "api://electiondata"


def _hmac_with_published_oct_key(server: FakeAuthorizationServer) -> str:
    server.extra_jwks.append(
        {"kty": "oct", "kid": "shared", "k": base64.urlsafe_b64encode(b"s" * 32).decode()}
    )
    return server.mint(key=b"s" * 32, algorithm="HS256", headers={"kid": "shared"})


def _unsigned(server: FakeAuthorizationServer) -> str:
    _, payload, _ = server.mint().split(".")
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","kid":"key-1"}').rstrip(b"=")
    return f"{none_header.decode()}.{payload}."


REJECTED: dict[str, Callable[[FakeAuthorizationServer], str]] = {
    "expired": lambda s: s.mint(exp=int(time.time()) - 120),
    "not yet valid": lambda s: s.mint(nbf=int(time.time()) + 120),
    "no exp": lambda s: s.mint(exp=None),
    "other issuer": lambda s: s.mint(iss="https://evil.example.com"),
    "other audience": lambda s: s.mint(aud="https://other.example.com/mcp"),
    "no audience": lambda s: s.mint(aud=None),
    "forged signature": lambda s: s.mint(key=OTHER_KEY),
    "unknown kid": lambda s: s.mint(headers={"kid": "nope"}),
    "alg none": _unsigned,
    "hmac with oct jwk": _hmac_with_published_oct_key,
    "not a jwt": lambda _s: "not-a-jwt",
}


@pytest.mark.parametrize("make_token", REJECTED.values(), ids=REJECTED.keys())
async def test_jwt_verifier_rejects(
    auth_server: FakeAuthorizationServer,
    make_token: Callable[[FakeAuthorizationServer], str],
) -> None:
    assert await _jwt_verifier().verify_token(make_token(auth_server)) is None


async def test_jwks_is_cached_and_refetched_after_rotation(
    auth_server: FakeAuthorizationServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier = _jwt_verifier()
    assert await verifier.verify_token(auth_server.mint()) is not None
    assert await verifier.verify_token(auth_server.mint()) is not None
    assert auth_server.jwks_fetches == 1

    auth_server.rotate()
    rotated = auth_server.mint()
    # Inside the retry window an unknown kid does not hammer the JWKS endpoint.
    assert await verifier.verify_token(rotated) is None
    assert auth_server.jwks_fetches == 1

    monkeypatch.setattr(oauth, "JWKS_RETRY_SECONDS", 0.0)
    verifier = _jwt_verifier()
    assert await verifier.verify_token(auth_server.mint()) is not None
    auth_server.rotate()
    assert await verifier.verify_token(auth_server.mint()) is not None
    assert auth_server.jwks_fetches == 3


async def test_jwks_endpoint_failure_rejects_the_token(
    auth_server: FakeAuthorizationServer,
) -> None:
    verifier = JWTTokenVerifier(f"{ISSUER}/missing", issuer=ISSUER, audience=RESOURCE)
    assert await verifier.verify_token(auth_server.mint()) is None


def test_outbound_calls_have_a_timeout() -> None:
    assert oauth._http_client().timeout == oauth.HTTP_TIMEOUT


@pytest.mark.parametrize(
    ("extra", "scopes"),
    [({}, []), ({"scope": 7}, []), ({"sub": 7}, None), ({"exp": "soon"}, None)],
    ids=["no scope", "non-string scope", "non-string sub", "non-numeric exp"],
)
def test_odd_claims_map_to_no_scopes_or_no_token(
    extra: dict[str, object], scopes: list[str] | None
) -> None:
    claims = {"iss": ISSUER, "aud": RESOURCE, **extra}

    access = oauth._access_token("t", claims, issuer=ISSUER, audience=RESOURCE)

    assert (access.scopes if access else None) == scopes
