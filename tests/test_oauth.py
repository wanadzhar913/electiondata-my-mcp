from __future__ import annotations

import json

import pytest
from mcp.server.auth.provider import AccessToken

from electiondata_my_mcp.oauth import (
    ENV_ISSUER_URL,
    ENV_REQUIRED_SCOPES,
    ENV_RESOURCE_URL,
    ENV_TOKEN,
    ENV_TOKENS,
    StaticTokenVerifier,
    apply_oauth_config,
    load_oauth_config,
)
from electiondata_my_mcp.server import mcp

pytestmark = pytest.mark.unit

ISSUER = "https://auth.example.com"
RESOURCE = "http://127.0.0.1:8000/mcp"
ALICE = {"alice-token": {"client_id": "alice", "scopes": ["electiondata:read"]}}


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_ISSUER_URL, raising=False)
    monkeypatch.delenv(ENV_RESOURCE_URL, raising=False)
    monkeypatch.delenv(ENV_REQUIRED_SCOPES, raising=False)
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_TOKENS, raising=False)


def test_oauth_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    assert load_oauth_config() is None


def test_load_oauth_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_ISSUER_URL, ISSUER)
    monkeypatch.setenv(ENV_RESOURCE_URL, RESOURCE)
    monkeypatch.setenv(ENV_REQUIRED_SCOPES, "electiondata:read, extra")
    monkeypatch.setenv(
        ENV_TOKENS,
        json.dumps(
            {
                "alice-token": {
                    "client_id": "alice",
                    "scopes": ["electiondata:read"],
                    "subject": "alice@example.com",
                }
            }
        ),
    )

    config = load_oauth_config()
    assert config is not None
    assert str(config.auth.issuer_url).rstrip("/") == ISSUER
    assert str(config.auth.resource_server_url).rstrip("/") == RESOURCE
    assert config.auth.required_scopes == ["electiondata:read", "extra"]


def test_omitted_scopes_default_to_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_ISSUER_URL, ISSUER)
    monkeypatch.setenv(ENV_TOKENS, json.dumps({"alice-token": {"client_id": "alice"}}))

    config = load_oauth_config()
    assert config is not None
    assert config.auth.required_scopes == ["electiondata:read"]


async def test_static_verifier_returns_known_token_and_rejects_unknown() -> None:
    known = AccessToken(token="alice-token", client_id="alice", scopes=["electiondata:read"])
    verifier = StaticTokenVerifier({"alice-token": known})
    assert await verifier.verify_token("alice-token") is known
    assert await verifier.verify_token("nope") is None


def test_apply_oauth_config_sets_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_ISSUER_URL, ISSUER)
    monkeypatch.setenv(ENV_TOKENS, json.dumps(ALICE))
    try:
        assert apply_oauth_config(mcp) is not None
        assert mcp.settings.auth is not None
        assert mcp._token_verifier is not None
        monkeypatch.delenv(ENV_ISSUER_URL)
        monkeypatch.delenv(ENV_TOKENS)
        assert apply_oauth_config(mcp) is None
        assert mcp.settings.auth is None
        assert mcp._token_verifier is None
    finally:
        monkeypatch.delenv(ENV_ISSUER_URL, raising=False)
        monkeypatch.delenv(ENV_TOKEN, raising=False)
        monkeypatch.delenv(ENV_TOKENS, raising=False)
        apply_oauth_config(mcp)


@pytest.mark.parametrize(
    ("issuer", "tokens", "scopes", "match"),
    [
        ("", json.dumps(ALICE), None, "MCP_OAUTH_TOKENS is set"),
        (ISSUER, "", None, "are empty"),
        (ISSUER, "not-json", None, "JSON object"),
        (ISSUER, "[]", None, "non-empty JSON object"),
        (ISSUER, "{}", None, "non-empty JSON object"),
        (ISSUER, json.dumps({"alice-token": "alice"}), None, "must be an object"),
        (ISSUER, json.dumps({"": {"client_id": "alice"}}), None, "non-empty token"),
        (ISSUER, json.dumps({"alice-token": {}}), None, "client_id"),
        (ISSUER, json.dumps({"alice-token": {"client_id": "alice", "scopes": []}}), None, "scopes"),
        (
            ISSUER,
            json.dumps({"alice-token": {"client_id": "alice", "scopes": [1]}}),
            None,
            "scopes",
        ),
        (
            ISSUER,
            json.dumps({"alice-token": {"client_id": "alice", "subject": 1}}),
            None,
            "subject",
        ),
        ("http://auth.example.com", json.dumps(ALICE), None, "HTTPS"),
        (ISSUER, json.dumps(ALICE), ",", "at least one scope"),
        ("not a url", json.dumps(ALICE), None, "Invalid OAuth URL"),
    ],
)
def test_load_oauth_config_rejects_bad_env(
    monkeypatch: pytest.MonkeyPatch,
    issuer: str,
    tokens: str,
    scopes: str | None,
    match: str,
) -> None:
    _clear(monkeypatch)
    if issuer:
        monkeypatch.setenv(ENV_ISSUER_URL, issuer)
    if tokens:
        monkeypatch.setenv(ENV_TOKENS, tokens)
    if scopes is not None:
        monkeypatch.setenv(ENV_REQUIRED_SCOPES, scopes)
    with pytest.raises(ValueError, match=match):
        load_oauth_config()


async def test_mcp_oauth_token_is_a_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_ISSUER_URL, ISSUER)
    monkeypatch.setenv(ENV_TOKEN, "s3cret")

    config = load_oauth_config()
    assert config is not None
    accepted = await config.verifier.verify_token("s3cret")
    assert accepted is not None
    assert accepted.client_id == "static"
    assert accepted.scopes == ["electiondata:read"]
    assert await config.verifier.verify_token("other") is None


def test_mcp_oauth_token_without_issuer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_TOKEN, "s3cret")
    with pytest.raises(ValueError, match="MCP_OAUTH_TOKEN is set"):
        load_oauth_config()


async def test_token_env_merges_with_token_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(ENV_ISSUER_URL, ISSUER)
    monkeypatch.setenv(ENV_TOKEN, "plain-token")
    monkeypatch.setenv(
        ENV_TOKENS,
        json.dumps({"plain-token": {"client_id": "alice"}, "other-token": {"client_id": "bob"}}),
    )

    config = load_oauth_config()
    assert config is not None
    plain = await config.verifier.verify_token("plain-token")
    other = await config.verifier.verify_token("other-token")
    assert plain is not None and plain.client_id == "alice"
    assert other is not None and other.client_id == "bob"
